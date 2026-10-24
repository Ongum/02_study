"""Collect one idempotent, timestamped Steam current-player snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.common.http import (
    HttpResponse,
    RetryPolicy,
    fetch_bytes_with_retry,
)
from entertainment_platform.common.jsonl import append_unique_jsonl
from entertainment_platform.connectors.steam.players import (
    CURRENT_PLAYERS_ENDPOINT,
    parse_player_count,
)

SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def run(
    *,
    appid: int,
    run_id: str,
    raw_directory: Path,
    output_path: Path,
    timeout_seconds: float = 20.0,
    retry_policy: RetryPolicy | None = None,
    observed_at: datetime | None = None,
    fetcher: Callable[[urllib.request.Request], HttpResponse] | None = None,
) -> dict[str, object]:
    if appid <= 0:
        raise ValueError("appid must be positive")
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only safe filename characters")
    existing = _find_run(output_path, appid=appid, run_id=run_id)
    if existing is not None:
        return {**existing, "rows_written": 0, "request_count": 0}

    query = urllib.parse.urlencode({"appid": appid})
    request = urllib.request.Request(
        f"{CURRENT_PLAYERS_ENDPOINT}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "entertainment-data-platform-players/0.0.1",
        },
        method="GET",
    )
    response = (
        fetcher(request)
        if fetcher is not None
        else fetch_bytes_with_retry(
            request,
            timeout_seconds=timeout_seconds,
            policy=retry_policy or RetryPolicy(),
        )
    )
    fetched_at = observed_at or datetime.now(UTC)
    body_sha256 = hashlib.sha256(response.body).hexdigest()
    raw_path = raw_directory / f"{run_id}-{body_sha256[:12]}.json"
    _write_once(raw_path, response.body)
    payload = json.loads(response.body)
    snapshot = parse_player_count(
        payload, appid=appid, observed_at=fetched_at, run_id=run_id
    )
    written = append_unique_jsonl(
        output_path,
        [snapshot.to_dict()],
        key_fields=("appid", "run_id"),
    )
    return {
        **snapshot.to_dict(),
        "rows_written": written,
        "request_count": response.attempts,
    }


def _find_run(path: Path, *, appid: int, run_id: str) -> dict[str, object] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError("player snapshot row must be an object")
            if row.get("appid") == appid and row.get("run_id") == run_id:
                return row
    return None


def _write_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite raw file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect one current-player snapshot.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--appid", type=int, default=1599340)
    parser.add_argument(
        "--raw-directory", type=Path, default=Path("raw_data/current_players")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output/fact_player_snapshot.jsonl")
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)
    result = run(
        appid=args.appid,
        run_id=args.run_id,
        raw_directory=args.raw_directory,
        output_path=args.output,
        timeout_seconds=args.timeout_seconds,
    )
    safe = {
        key: result[key]
        for key in (
            "appid",
            "observed_at",
            "player_count",
            "rows_written",
            "request_count",
        )
    }
    print(json.dumps(safe, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
