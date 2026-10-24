"""One-request, privacy-conscious Steam review API smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from entertainment_platform.common.http import (
    HttpFetchError,
    RetryPolicy,
    fetch_bytes_with_retry,
)
from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.api import build_review_request_url
from entertainment_platform.connectors.steam.reviews import (
    STEAM_REVIEWS_ENDPOINT,
    SteamReviewValidationError,
    parse_review_page,
)
from entertainment_platform.jobs.offline_steam_reviews import DEDUPLICATION_KEY

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SAFE_RESPONSE_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Cache-Control",
    "Age",
    "Retry-After",
)


def run(
    *,
    raw_directory: Path,
    normalized_path: Path,
    metadata_path: Path,
    run_id: str,
    appid: int = 1599340,
    timeout_seconds: float = 20.0,
    cursor: str = "*",
    num_per_page: int = 1,
    filter_value: int = 1,
    date_range_start: int | None = None,
    date_range_end: int | None = None,
    retry_policy: RetryPolicy | None = None,
) -> dict[str, object]:
    """Execute exactly one anonymous request and persist sanitized evidence."""

    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only safe filename characters")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be in (0, 60]")

    url, parameters = build_review_request_url(
        appid=appid,
        cursor=cursor,
        num_per_page=num_per_page,
        filter_value=filter_value,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )
    safe_parameters = _sanitize_request_parameters(parameters)
    requested_at = datetime.now(UTC)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "entertainment-data-platform-smoke/0.0.1",
        },
        method="GET",
    )

    try:
        fetched = fetch_bytes_with_retry(
            request,
            timeout_seconds=timeout_seconds,
            policy=retry_policy or RetryPolicy(),
        )
    except HttpFetchError as exc:
        _write_metadata(
            metadata_path,
            {
                "run_id": run_id,
                "appid": appid,
                "requested_at": _utc_iso(requested_at),
                "http_status": exc.status,
                "validation_status": "http_error",
                "request_parameters": safe_parameters,
                "request_count": exc.attempts,
                "retryable": exc.retryable,
                "error_kind": exc.error_kind,
                "recommended_delay_seconds": exc.recommended_delay_seconds,
            },
        )
        raise

    status = fetched.status
    body = fetched.body
    response_headers = {
        name.lower(): value
        for name, value in fetched.headers.items()
        if name.lower() in {header.lower() for header in SAFE_RESPONSE_HEADERS}
    }

    fetched_at = datetime.now(UTC)
    raw_path = raw_directory / f"{run_id}.json"
    _write_bytes(raw_path, body)

    metadata: dict[str, object] = {
        "run_id": run_id,
        "appid": appid,
        "source_endpoint": STEAM_REVIEWS_ENDPOINT,
        "request_parameters": safe_parameters,
        "requested_at": _utc_iso(requested_at),
        "fetched_at": _utc_iso(fetched_at),
        "http_status": status,
        "response_bytes": len(body),
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_headers": response_headers,
        "request_count": fetched.attempts,
        "retry_delays_seconds": list(fetched.retry_delays_seconds),
    }

    try:
        payload = json.loads(body)
        metadata["top_level_fields"] = (
            sorted(payload) if isinstance(payload, dict) else []
        )
        parsed = parse_review_page(
            payload,
            appid=appid,
            fetched_at=fetched_at,
            run_id=run_id,
        )
        written = append_unique_jsonl(
            normalized_path,
            (review.to_dict() for review in parsed.reviews),
            key_fields=DEDUPLICATION_KEY,
        )
        metadata.update(
            {
                "validation_status": "passed",
                "review_count": len(parsed.reviews),
                "normalized_rows_written": written,
                "cursor_present": bool(parsed.cursor),
                "total_matching": parsed.total_matching,
            }
        )
    except (json.JSONDecodeError, SteamReviewValidationError) as exc:
        metadata.update(
            {
                "validation_status": "failed",
                "validation_error_type": type(exc).__name__,
                "validation_error": str(exc),
            }
        )
        _write_metadata(metadata_path, metadata)
        raise

    _write_metadata(metadata_path, metadata)
    return metadata


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _write_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(metadata), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    _write_bytes(path, encoded + b"\n")


def _sanitize_request_parameters(parameters: Mapping[str, object]) -> dict[str, object]:
    sanitized = dict(parameters)
    cursor = sanitized.pop("cursor", None)
    sanitized["cursor_kind"] = "initial" if cursor == "*" else "returned"
    if isinstance(cursor, str) and cursor != "*":
        sanitized["cursor_sha256"] = hashlib.sha256(cursor.encode("utf-8")).hexdigest()
    return sanitized


def _read_cursor(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError("cursor source must contain a JSON object")
    response = payload.get("response")
    if not isinstance(response, dict):
        raise TypeError("cursor source is missing response envelope")
    cursor = response.get("cursor")
    if not isinstance(cursor, str) or not cursor:
        raise ValueError("cursor source is missing a non-empty cursor")
    return cursor


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make one anonymous Steam review request without logging content."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument("--raw-directory", type=Path, default=Path("raw_data/smoke"))
    parser.add_argument(
        "--normalized-output",
        type=Path,
        default=Path("output/smoke_normalized_reviews.jsonl"),
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=Path("output/smoke_metadata.json"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument(
        "--cursor-source",
        type=Path,
        help="Saved prior raw response whose cursor should be used without logging it.",
    )
    args = parser.parse_args(argv)

    cursor = _read_cursor(args.cursor_source) if args.cursor_source else "*"

    metadata = run(
        raw_directory=args.raw_directory,
        normalized_path=args.normalized_output,
        metadata_path=args.metadata_output,
        run_id=args.run_id,
        appid=args.appid,
        timeout_seconds=args.timeout_seconds,
        cursor=cursor,
    )
    safe_summary = {
        key: metadata[key]
        for key in (
            "run_id",
            "http_status",
            "response_bytes",
            "response_sha256",
            "top_level_fields",
            "validation_status",
            "review_count",
            "normalized_rows_written",
            "cursor_present",
            "total_matching",
            "request_count",
        )
        if key in metadata
    }
    print(json.dumps(safe_summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
