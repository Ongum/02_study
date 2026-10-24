"""Run a resumable, one-shot Steam review history backfill on GCP.

The operator starts one Cloud Run Job. Internally, the job splits the fixed
creation-time range into calendar months and persists each partition after a
bounded group of pages. A retry reuses the same fixed specification and resumes
from GCS checkpoints instead of restarting the roughly 200k-row collection.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from entertainment_platform.cloud.gcp import (
    GcpClients,
    acquire_lock,
    clients_from_adc,
    download_if_present,
    release_lock,
    replace_jsonl_table,
    upload_immutable,
    upload_state,
)
from entertainment_platform.jobs.steam_reviews_backfill import run_backfill

STATE_ROOT = "state/review_backfill"
BRONZE_ROOT = "bronze/steam/review_backfill"
TABLE_ID = "silver_review_history"
BackfillRunner = Callable[..., object]


def run_gcp_review_backfill(
    *,
    project_id: str,
    bucket_name: str,
    dataset: str,
    range_start: datetime,
    range_end: datetime,
    clients: GcpClients,
    execution_id: str,
    appid: int = 1599340,
    max_pages_per_chunk: int = 50,
    page_size: int = 100,
    request_interval_seconds: float = 1.0,
    timeout_seconds: float = 20.0,
    max_total_pages: int = 5000,
    backfill_runner: BackfillRunner = run_backfill,
    now: datetime | None = None,
) -> dict[str, object]:
    """Collect a fixed historical range and replace its BigQuery Silver table."""

    start = _require_utc(range_start, "range_start")
    end = _require_utc(range_end, "range_end")
    if start > end:
        raise ValueError("range_start must not exceed range_end")
    if not 1 <= max_pages_per_chunk <= 100:
        raise ValueError("max_pages_per_chunk must be in [1, 100]")
    if max_total_pages < 1:
        raise ValueError("max_total_pages must be positive")

    observed_at = _require_utc(now or datetime.now(UTC), "now")
    safe_execution_id = _safe_token(execution_id)
    bucket = clients.storage.bucket(bucket_name)
    lock_name = "locks/review_backfill.json"
    lock_generation = acquire_lock(
        bucket,
        lock_name,
        {
            "source": "review_backfill",
            "execution_id": safe_execution_id,
            "acquired_at": _utc_iso(observed_at),
        },
    )
    try:
        with tempfile.TemporaryDirectory(prefix="steam-review-backfill-") as temporary:
            root = Path(temporary)
            specification = _load_or_create_specification(
                bucket=bucket,
                root=root,
                appid=appid,
                range_start=start,
                range_end=end,
                observed_at=observed_at,
            )
            fixed_fetched_at = _parse_utc(str(specification["started_at"]))
            partition_summaries: list[dict[str, object]] = []
            total_pages = 0

            for partition_start, partition_end in monthly_partitions(start, end):
                partition_key = partition_start.strftime("%Y-%m")
                partition_root = root / "partitions" / partition_key
                history_path = partition_root / "data.jsonl"
                checkpoint_path = partition_root / "checkpoint.json"
                raw_directory = partition_root / "raw"
                state_prefix = f"{STATE_ROOT}/partitions/{partition_key}"
                download_if_present(bucket, f"{state_prefix}/data.jsonl", history_path)
                download_if_present(
                    bucket, f"{state_prefix}/checkpoint.json", checkpoint_path
                )
                history_path.parent.mkdir(parents=True, exist_ok=True)
                history_path.touch(exist_ok=True)

                while True:
                    result = backfill_runner(
                        appid=appid,
                        range_start=partition_start,
                        range_end=partition_end,
                        raw_directory=raw_directory,
                        history_path=history_path,
                        checkpoint_path=checkpoint_path,
                        run_id=f"lostark-review-backfill-{partition_key}",
                        max_pages_per_run=max_pages_per_chunk,
                        page_size=page_size,
                        request_interval_seconds=request_interval_seconds,
                        timeout_seconds=timeout_seconds,
                        fetched_at=fixed_fetched_at,
                    )
                    result_dict = _result_dict(result)
                    pages_this_chunk = int(result_dict["pages_fetched_this_run"])
                    total_pages += pages_this_chunk
                    if total_pages > max_total_pages:
                        raise RuntimeError("review backfill exceeded max_total_pages")

                    raw_files = [
                        path for path in raw_directory.rglob("*") if path.is_file()
                    ]
                    for path in raw_files:
                        relative = path.relative_to(raw_directory).as_posix()
                        upload_immutable(
                            bucket,
                            f"{BRONZE_ROOT}/{partition_key}/{relative}",
                            path,
                        )

                    # Data is replaced before the cursor. If the cursor upload
                    # fails, replaying from the older cursor is deduplicated.
                    upload_state(bucket, f"{state_prefix}/data.jsonl", history_path)
                    if not checkpoint_path.exists():
                        raise RuntimeError("backfill did not write a checkpoint")
                    upload_state(
                        bucket, f"{state_prefix}/checkpoint.json", checkpoint_path
                    )
                    shutil.rmtree(raw_directory, ignore_errors=True)

                    if bool(result_dict["completed"]):
                        quality = _validate_completed_partition(result_dict)
                        partition_summaries.append(
                            {
                                "partition": partition_key,
                                "range_start": _utc_iso(partition_start),
                                "range_end": _utc_iso(partition_end),
                                "rows": int(result_dict["unique_reviews_stored"]),
                                "pages": int(result_dict["pages_committed_total"]),
                                "total_matching": int(
                                    result_dict["total_matching_reported"]
                                ),
                                "api_unreturned_rows": quality["api_unreturned_rows"],
                            }
                        )
                        break
                    if pages_this_chunk == 0:
                        raise RuntimeError("incomplete backfill chunk made no progress")

            combined_path = root / "silver_review_history.jsonl"
            combined = _combine_partitions(
                root=root,
                partitions=partition_summaries,
                destination=combined_path,
                appid=appid,
            )
            expected_rows = sum(
                int(partition["rows"]) for partition in partition_summaries
            )
            if combined["rows"] != expected_rows:
                raise RuntimeError(
                    "combined row count does not match validated partition row sum"
                )
            api_reported_rows = sum(
                int(partition["total_matching"]) for partition in partition_summaries
            )

            table = f"{project_id}.{dataset}.{TABLE_ID}"
            load_job_id = replace_jsonl_table(
                clients, source=combined_path, destination=table
            )
            manifest = {
                "schema_version": 1,
                "source": "review_backfill",
                "execution_id": safe_execution_id,
                "appid": appid,
                "range_start": _utc_iso(start),
                "range_end": _utc_iso(end),
                "started_at": specification["started_at"],
                "completed_at": _utc_iso(datetime.now(UTC)),
                "partition_count": len(partition_summaries),
                "pages_fetched_this_execution": total_pages,
                "row_count": combined["rows"],
                "duplicate_review_versions": combined["duplicate_review_versions"],
                "expected_rows_from_api": api_reported_rows,
                "api_unreturned_rows": api_reported_rows - combined["rows"],
                "bigquery_table": table,
                "bigquery_load_job_id": load_job_id,
                "partitions": partition_summaries,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            upload_immutable(
                bucket,
                f"manifests/review_backfill/{safe_execution_id}.json",
                manifest_path,
            )
            upload_state(bucket, f"{STATE_ROOT}/last_success.json", manifest_path)
            return manifest
    finally:
        release_lock(bucket, lock_name, lock_generation)


def monthly_partitions(
    range_start: datetime, range_end: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    """Return non-overlapping inclusive calendar-month UTC partitions."""

    start = _require_utc(range_start, "range_start")
    end = _require_utc(range_end, "range_end")
    if start > end:
        raise ValueError("range_start must not exceed range_end")
    partitions: list[tuple[datetime, datetime]] = []
    current = start
    while current <= end:
        if current.month == 12:
            next_month = datetime(current.year + 1, 1, 1, tzinfo=UTC)
        else:
            next_month = datetime(current.year, current.month + 1, 1, tzinfo=UTC)
        partition_end = min(end, next_month - timedelta(seconds=1))
        partitions.append((current, partition_end))
        current = partition_end + timedelta(seconds=1)
    return tuple(partitions)


def _load_or_create_specification(
    *,
    bucket: Any,
    root: Path,
    appid: int,
    range_start: datetime,
    range_end: datetime,
    observed_at: datetime,
) -> dict[str, object]:
    path = root / "spec.json"
    found = download_if_present(bucket, f"{STATE_ROOT}/spec.json", path)
    if found:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "schema_version": 1,
            "appid": appid,
            "range_start": _utc_iso(range_start),
            "range_end": _utc_iso(range_end),
            "partitioning": "calendar_month_utc",
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ValueError(f"existing backfill specification differs: {key}")
        _parse_utc(str(payload["started_at"]))
        return payload

    payload = {
        "schema_version": 1,
        "appid": appid,
        "range_start": _utc_iso(range_start),
        "range_end": _utc_iso(range_end),
        "partitioning": "calendar_month_utc",
        "started_at": _utc_iso(observed_at),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    upload_state(bucket, f"{STATE_ROOT}/spec.json", path)
    return payload


def _combine_partitions(
    *,
    root: Path,
    partitions: list[dict[str, object]],
    destination: Path,
    appid: int,
) -> dict[str, int]:
    keys: set[tuple[object, object, object]] = set()
    rows = 0
    duplicates = 0
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        for partition in partitions:
            source = root / "partitions" / str(partition["partition"]) / "data.jsonl"
            with source.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError(
                            f"partition row is not an object: {source}:{line_number}"
                        )
                    if row.get("appid") != appid:
                        raise ValueError("partition row appid does not match")
                    key = (
                        row.get("appid"),
                        row.get("recommendationid"),
                        row.get("updated_at"),
                    )
                    if None in key:
                        raise ValueError("partition row is missing its version key")
                    if key in keys:
                        duplicates += 1
                        continue
                    keys.add(key)
                    output.write(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    rows += 1
    return {"rows": rows, "duplicate_review_versions": duplicates}


def _validate_completed_partition(result: dict[str, Any]) -> dict[str, int]:
    total = result.get("total_matching_reported")
    if total is None:
        raise RuntimeError("completed partition has no total_matching")
    observed = int(result["rows_observed_total"])
    unique_reviews = int(result["unique_reviews_stored"])
    reported = int(total)
    if observed != unique_reviews:
        raise RuntimeError(
            "completed partition observed rows do not match unique stored reviews"
        )
    if reported < observed:
        raise RuntimeError("completed partition exceeds API total_matching")
    if int(result["duplicate_versions_in_storage"]) != 0:
        raise RuntimeError("completed partition contains duplicate review versions")
    if int(result["out_of_range_rows"]) != 0:
        raise RuntimeError("completed partition contains out-of-range rows")
    if int(result["invalid_timestamp_rows"]) != 0:
        raise RuntimeError("completed partition contains invalid timestamps")
    return {"api_unreturned_rows": reported - observed}


def _result_dict(value: object) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return dict(asdict(value))
    if isinstance(value, dict):
        return value
    raise TypeError("backfill result must be a dataclass or dictionary")


def _safe_token(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    ).strip(".-_")
    if not safe:
        raise ValueError("execution_id has no safe characters")
    return safe[:100]


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _parse_utc(value: str) -> datetime:
    return _require_utc(datetime.fromisoformat(value), "timestamp")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run or resume one fixed-range GCP Steam review backfill."
    )
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--max-pages-per-chunk", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-interval-seconds", type=float, default=1.0)
    parser.add_argument("--max-total-pages", type=int, default=5000)
    args = parser.parse_args(argv)

    project_id = _required_env("GCP_PROJECT_ID")
    manifest = run_gcp_review_backfill(
        project_id=project_id,
        bucket_name=_required_env("GCP_BUCKET"),
        dataset=os.getenv("BQ_DATASET", "game_analytics"),
        range_start=args.start,
        range_end=args.end,
        clients=clients_from_adc(project_id),
        execution_id=os.getenv(
            "CLOUD_RUN_EXECUTION", f"manual-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        ),
        appid=int(os.getenv("STEAM_APP_ID", "1599340")),
        max_pages_per_chunk=args.max_pages_per_chunk,
        page_size=args.page_size,
        request_interval_seconds=args.request_interval_seconds,
        max_total_pages=args.max_total_pages,
    )
    safe_summary = {
        "source": manifest["source"],
        "execution_id": manifest["execution_id"],
        "range_start": manifest["range_start"],
        "range_end": manifest["range_end"],
        "partition_count": manifest["partition_count"],
        "row_count": manifest["row_count"],
        "expected_rows_from_api": manifest["expected_rows_from_api"],
        "bigquery_table": manifest["bigquery_table"],
    }
    print(json.dumps(safe_summary, sort_keys=True))
    return 0


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
