"""Run the three bounded Steam incremental collectors with one local lock.

This is an operational wrapper, not a scheduler.  It keeps the existing
source-specific collectors independent while providing one safe command for
manual runs.  A failure in one source is recorded and does not prevent the
other sources from being attempted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from entertainment_platform.jobs.steam_current_players import run as run_players
from entertainment_platform.jobs.steam_news_backfill import run_news_backfill
from entertainment_platform.jobs.steam_reviews_incremental import run_incremental

Runner = Callable[..., object]
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class CollectionLockError(RuntimeError):
    """Raised when another local incremental collection already owns the lock."""


def run_local_incremental_collection(
    *,
    config_path: Path,
    root: Path,
    run_id: str,
    as_of: datetime | None = None,
    reviews_runner: Runner = run_incremental,
    players_runner: Runner = run_players,
    news_runner: Runner = run_news_backfill,
) -> dict[str, object]:
    """Attempt reviews, players and news once and write a truthful manifest.

    ``as_of`` is injectable so tests and manual replays can use a reproducible
    clock.  In normal operation it defaults to the actual current UTC time.
    """

    root = root.resolve()
    config = _read_config(config_path)
    supplied_clock = as_of or datetime.now(UTC)
    if supplied_clock.tzinfo is None or supplied_clock.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must contain only safe filename characters")
    observed_at = supplied_clock.astimezone(UTC)

    lock_path = _inside_root(root, str(config["lock_path"]))
    manifest_dir = _inside_root(root, str(config["manifest_directory"]))
    manifest_path = manifest_dir / f"{run_id}.json"
    token = uuid.uuid4().hex
    started_at = datetime.now(UTC)
    _acquire_lock(lock_path, run_id=run_id, token=token, acquired_at=started_at)

    steps: list[dict[str, object]] = []
    try:
        reviews = _mapping(config, "reviews")
        _attempt(
            steps,
            "reviews_updated",
            reviews_runner,
            raw_directory=_inside_root(root, str(reviews["raw_directory"])),
            normalized_path=_inside_root(root, str(reviews["normalized_output"])),
            checkpoint_path=_inside_root(root, str(reviews["checkpoint"])),
            run_id=f"{run_id}-reviews",
            appid=int(config["appid"]),
            max_pages=int(reviews["max_pages"]),
            page_size=int(reviews["page_size"]),
            timeout_seconds=float(reviews["timeout_seconds"]),
            fetched_at=observed_at,
        )

        players = _mapping(config, "players")
        _attempt(
            steps,
            "current_players",
            players_runner,
            appid=int(config["appid"]),
            run_id=f"{run_id}-players",
            raw_directory=_inside_root(root, str(players["raw_directory"])),
            output_path=_inside_root(root, str(players["output"])),
            timeout_seconds=float(players["timeout_seconds"]),
            observed_at=observed_at,
        )

        news = _mapping(config, "news")
        lookback = timedelta(hours=float(news["lookback_hours"]))
        _attempt(
            steps,
            "app_news",
            news_runner,
            appid=int(config["appid"]),
            range_start=observed_at - lookback,
            range_end=observed_at,
            run_id=f"{run_id}-news",
            raw_directory=_inside_root(root, str(news["raw_directory"])),
            output_path=_inside_root(root, str(news["output"])),
            checkpoint_path=_inside_root(root, str(news["checkpoint_directory"]))
            / f"{run_id}.json",
            max_pages_per_run=int(news["max_pages"]),
            page_size=int(news["page_size"]),
            request_interval_seconds=float(news["request_interval_seconds"]),
            timeout_seconds=float(news["timeout_seconds"]),
            fetched_at=observed_at,
        )

        failed = [step for step in steps if step["status"] != "complete"]
        manifest: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_id,
            "appid": int(config["appid"]),
            "observed_at": _utc_iso(observed_at),
            "started_at": _utc_iso(started_at),
            "finished_at": _utc_iso(datetime.now(UTC)),
            "status": "complete" if not failed else "partial_failure",
            "steps": steps,
            "cloud_resources_created": False,
        }
        _write_json_atomic(manifest_path, manifest)
        return manifest
    finally:
        _release_lock(lock_path, token=token)


def _attempt(
    steps: list[dict[str, object]], name: str, runner: Runner, **kwargs: object
) -> None:
    started = monotonic()
    try:
        result = _result_dict(runner(**kwargs))
        complete = bool(result.get("complete", True))
        steps.append(
            {
                "name": name,
                "status": "complete" if complete else "incomplete",
                "duration_seconds": round(monotonic() - started, 6),
                "result": result,
            }
        )
    # The connector boundary intentionally catches any source-specific failure:
    # independent player/news collection must still run and the manifest keeps
    # the exact exception type without falsely claiming this step succeeded.
    except Exception as exc:  # noqa: BLE001
        steps.append(
            {
                "name": name,
                "status": "failed",
                "duration_seconds": round(monotonic() - started, 6),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )


def _result_dict(value: object) -> dict[str, object]:
    if is_dataclass(value) and not isinstance(value, type):
        return dict(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise TypeError("collector result must be a dataclass or mapping")


def _acquire_lock(
    path: Path, *, run_id: str, token: str, acquired_at: datetime
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "run_id": run_id,
            "token": token,
            "pid": os.getpid(),
            "acquired_at": _utc_iso(acquired_at),
        },
        sort_keys=True,
    ).encode()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise CollectionLockError(f"collection lock already exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _release_lock(path: Path, *, token: str) -> None:
    if not path.exists():
        return
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    # Never remove a lock that no longer belongs to this process/run.
    if owner.get("token") == token:
        path.unlink()


def _read_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("collection config must be a JSON object")
    required = {
        "appid",
        "lock_path",
        "manifest_directory",
        "reviews",
        "players",
        "news",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ValueError(f"collection config is missing keys: {missing}")
    if int(value["appid"]) <= 0:
        raise ValueError("appid must be positive")
    return value


def _mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} config must be an object")
    return value


def _inside_root(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"configured path escapes project root: {value}")
    return candidate


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one locked local Steam incremental collection cycle."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/local_incremental_collection.json"),
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    args = parser.parse_args(argv)
    manifest = run_local_incremental_collection(
        config_path=args.config,
        root=args.root,
        run_id=args.run_id,
        as_of=args.as_of,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
