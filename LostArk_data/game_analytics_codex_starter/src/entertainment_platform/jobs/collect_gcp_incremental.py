"""Run one Steam collector in Cloud Run with GCS state and BigQuery Silver."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
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
from entertainment_platform.jobs.steam_current_players import run as run_players
from entertainment_platform.jobs.steam_news_backfill import run_news_backfill
from entertainment_platform.jobs.steam_reviews_incremental import run_incremental

SOURCES = ("reviews", "players", "news")
SCHEDULED = "scheduled"
INTERVALS = {
    "players": timedelta(minutes=50),
    "reviews": timedelta(hours=5, minutes=50),
    "news": timedelta(hours=23, minutes=50),
}
TABLES = {
    "reviews": "silver_review_history_updates",
    "players": "fact_player_snapshot",
    "news": "silver_app_news",
}


def run_gcp_collection(
    *,
    source: str,
    project_id: str,
    bucket_name: str,
    dataset: str,
    clients: GcpClients,
    now: datetime | None = None,
    execution_id: str | None = None,
    appid: int = 1599340,
) -> dict[str, object]:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    run_id = _safe_run_id(execution_id or f"manual-{observed_at:%Y%m%dT%H%M%SZ}")
    bucket = clients.storage.bucket(bucket_name)
    lock_name = f"locks/{source}.json"
    lock_generation = acquire_lock(
        bucket,
        lock_name,
        {
            "source": source,
            "run_id": run_id,
            "acquired_at": _utc_iso(observed_at),
        },
    )
    try:
        with tempfile.TemporaryDirectory(prefix=f"steam-{source}-") as temporary:
            root = Path(temporary)
            output = root / "state" / f"{source}.jsonl"
            checkpoint = root / "state" / f"{source}-checkpoint.json"
            raw_directory = root / "raw"
            state_prefix = f"state/{source}"
            download_if_present(bucket, f"{state_prefix}/data.jsonl", output)
            checkpoint_found = download_if_present(
                bucket, f"{state_prefix}/checkpoint.json", checkpoint
            )

            # A successful source run may legitimately yield zero rows. Keep
            # that empty canonical state explicit so its GCS upload and
            # manifest do not depend on a collector creating the file.
            output.parent.mkdir(parents=True, exist_ok=True)
            output.touch(exist_ok=True)

            result = _run_source(
                source=source,
                appid=appid,
                run_id=run_id,
                observed_at=observed_at,
                raw_directory=raw_directory,
                output=output,
                checkpoint=checkpoint,
                checkpoint_found=checkpoint_found,
            )
            raw_files = [path for path in raw_directory.rglob("*") if path.is_file()]
            for path in raw_files:
                relative = path.relative_to(raw_directory).as_posix()
                upload_immutable(
                    bucket,
                    f"bronze/steam/{source}/{run_id}/{relative}",
                    path,
                )

            # Persist data before the checkpoint: a replay after an interruption
            # is safe because local JSONL writes deduplicate by source keys.
            upload_state(bucket, f"{state_prefix}/data.jsonl", output)
            if checkpoint.exists():
                upload_state(bucket, f"{state_prefix}/checkpoint.json", checkpoint)

            table = f"{project_id}.{dataset}.{TABLES[source]}"
            load_job_id = replace_jsonl_table(clients, source=output, destination=table)
            manifest = {
                "schema_version": 1,
                "source": source,
                "run_id": run_id,
                "observed_at": _utc_iso(observed_at),
                "raw_objects_written": len(raw_files),
                "state_bytes": output.stat().st_size,
                "bigquery_table": table,
                "bigquery_load_job_id": load_job_id,
                "result": _to_dict(result),
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            upload_immutable(
                bucket,
                f"manifests/{source}/{run_id}.json",
                manifest_path,
            )
            marker_path = root / "last_success.json"
            marker_path.write_text(
                json.dumps(
                    {"run_id": run_id, "completed_at": _utc_iso(datetime.now(UTC))},
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            upload_state(
                bucket,
                f"{state_prefix}/last_success.json",
                marker_path,
            )
            return manifest
    finally:
        release_lock(bucket, lock_name, lock_generation)


def _run_source(
    *,
    source: str,
    appid: int,
    run_id: str,
    observed_at: datetime,
    raw_directory: Path,
    output: Path,
    checkpoint: Path,
    checkpoint_found: bool,
) -> object:
    if source == "reviews":
        if not checkpoint_found:
            raise RuntimeError("reviews bootstrap checkpoint is missing from GCS state")
        return run_incremental(
            raw_directory=raw_directory,
            normalized_path=output,
            checkpoint_path=checkpoint,
            run_id=run_id,
            appid=appid,
            max_pages=20,
            page_size=100,
            timeout_seconds=20.0,
            fetched_at=observed_at,
        )
    if source == "players":
        return run_players(
            appid=appid,
            run_id=run_id,
            raw_directory=raw_directory,
            output_path=output,
            timeout_seconds=20.0,
            observed_at=observed_at,
        )
    return run_news_backfill(
        appid=appid,
        range_start=observed_at - timedelta(hours=48),
        range_end=observed_at,
        run_id=run_id,
        raw_directory=raw_directory,
        output_path=output,
        checkpoint_path=checkpoint,
        max_pages_per_run=3,
        page_size=100,
        request_interval_seconds=1.0,
        timeout_seconds=20.0,
        fetched_at=observed_at,
    )


def _safe_run_id(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value
    )
    safe = safe.strip(".-_")
    if not safe:
        raise ValueError("execution id has no safe characters")
    return safe[:80]


def due_sources(*, bucket: Any, now: datetime, root: Path) -> list[str]:
    """Return sources whose last successful cloud run is outside its cadence."""

    due: list[str] = []
    for source in SOURCES:
        marker = root / f"{source}-last-success.json"
        found = download_if_present(bucket, f"state/{source}/last_success.json", marker)
        if not found:
            due.append(source)
            continue
        payload = json.loads(marker.read_text(encoding="utf-8"))
        completed_at = datetime.fromisoformat(str(payload["completed_at"]))
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("last-success timestamp must be timezone-aware")
        if now - completed_at.astimezone(UTC) >= INTERVALS[source]:
            due.append(source)
    return due


def _to_dict(value: object) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return dict(asdict(value))
    if isinstance(value, dict):
        return value
    raise TypeError("collector result must be a dataclass or dictionary")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one GCP-backed collector.")
    parser.add_argument("--source", choices=(*SOURCES, SCHEDULED), required=True)
    args = parser.parse_args(argv)
    project_id = _required_env("GCP_PROJECT_ID")
    clients = clients_from_adc(project_id)
    bucket_name = _required_env("GCP_BUCKET")
    observed_at = datetime.now(UTC)
    if args.source == SCHEDULED:
        with tempfile.TemporaryDirectory(prefix="steam-schedule-") as temporary:
            selected_sources = due_sources(
                bucket=clients.storage.bucket(bucket_name),
                now=observed_at,
                root=Path(temporary),
            )
    else:
        selected_sources = [args.source]
    summaries: list[dict[str, object]] = []
    failed = False
    for source in selected_sources:
        try:
            manifest = run_gcp_collection(
                source=source,
                project_id=project_id,
                bucket_name=bucket_name,
                dataset=os.getenv("BQ_DATASET", "game_analytics"),
                clients=clients,
                now=observed_at,
                execution_id=os.getenv("CLOUD_RUN_EXECUTION"),
                appid=int(os.getenv("STEAM_APP_ID", "1599340")),
            )
            summaries.append(
                {
                    "source": manifest["source"],
                    "status": "complete",
                    "run_id": manifest["run_id"],
                    "raw_objects_written": manifest["raw_objects_written"],
                    "state_bytes": manifest["state_bytes"],
                    "bigquery_table": manifest["bigquery_table"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - independent scheduled sources
            failed = True
            summaries.append(
                {
                    "source": source,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )
    safe_summary = {
        "mode": args.source,
        "sources_due": selected_sources,
        "sources": summaries,
    }
    print(json.dumps(safe_summary, sort_keys=True))
    return 2 if failed else 0


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
