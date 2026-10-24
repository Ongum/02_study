"""Dependency-free, durable JSON Lines storage helpers."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


class JsonlValidationError(ValueError):
    """Raised when an existing JSONL file cannot be safely read."""


def append_unique_jsonl(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    key_fields: Sequence[str],
    known_keys: set[tuple[Any, ...]] | None = None,
) -> int:
    """Append rows whose compound key is not already present.

    The existing file is fully validated before it is opened for append. The
    caller can therefore retry the same validated batch without duplicating it.
    A successful return occurs only after the appended bytes are flushed and
    synced to disk.
    """

    if not key_fields:
        raise ValueError("key_fields must not be empty")

    existing_keys = (
        known_keys if known_keys is not None else read_jsonl_keys(path, key_fields)
    )
    pending: list[dict[str, Any]] = []
    batch_keys: set[tuple[Any, ...]] = set()

    for row in rows:
        materialized = dict(row)
        key = _row_key(materialized, key_fields)
        if key in existing_keys or key in batch_keys:
            continue
        pending.append(materialized)
        batch_keys.add(key)

    if not pending:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in pending:
            handle.write(
                json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    if known_keys is not None:
        known_keys.update(batch_keys)
    return len(pending)


def read_jsonl_keys(path: Path, key_fields: Sequence[str]) -> set[tuple[Any, ...]]:
    if not path.exists():
        return set()

    keys: set[tuple[Any, ...]] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JsonlValidationError(
                    f"invalid JSONL at {path}:{line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise JsonlValidationError(f"expected object at {path}:{line_number}")
            keys.add(_row_key(row, key_fields))
    return keys


def _row_key(row: Mapping[str, Any], key_fields: Sequence[str]) -> tuple[Any, ...]:
    missing = [field for field in key_fields if field not in row]
    if missing:
        raise JsonlValidationError(f"row is missing key fields: {', '.join(missing)}")
    return tuple(row[field] for field in key_fields)
