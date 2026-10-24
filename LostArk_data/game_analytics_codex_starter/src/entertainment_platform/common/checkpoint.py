"""Small atomic JSON checkpoint store shared by source connectors."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class CheckpointValidationError(ValueError):
    """Raised when a checkpoint is unreadable or violates its contract."""


def read_json_checkpoint(path: Path) -> dict[str, Any] | None:
    """Read one checkpoint object, returning ``None`` before the first commit."""

    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointValidationError(f"invalid checkpoint: {path}") from exc
    if not isinstance(value, dict):
        raise CheckpointValidationError("checkpoint root must be a JSON object")
    return value


def write_json_checkpoint(path: Path, value: Mapping[str, Any]) -> None:
    """Durably replace a checkpoint after the caller finishes data writes.

    Writing a sibling temporary file and replacing the destination prevents a
    process interruption from leaving a half-written checkpoint. Callers must
    still obey the transaction boundary: durable data first, checkpoint second.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    encoded = json.dumps(
        dict(value), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
