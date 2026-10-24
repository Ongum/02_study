"""Checkpoint contract for Steam's reverse-ordered Updated review stream."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from entertainment_platform.common.checkpoint import (
    CheckpointValidationError,
    read_json_checkpoint,
    write_json_checkpoint,
)

CHECKPOINT_SCHEMA_VERSION = 1
UPDATED_STREAM_FILTER = 2


@dataclass(frozen=True, slots=True)
class UpdatedReviewCheckpoint:
    """Durable high-watermark plus a configurable replay overlap.

    Steam returns the Updated stream newest first. A future incremental scan
    starts at the first page and continues until it crosses
    ``high_watermark_at - overlap_seconds``. The overlap intentionally replays
    some rows; storage deduplication makes that replay safe.
    """

    schema_version: int
    appid: int
    filter_value: int
    high_watermark_at: str
    overlap_seconds: int
    committed_at: str
    run_id: str

    @property
    def scan_floor(self) -> datetime:
        return _parse_utc(self.high_watermark_at) - timedelta(
            seconds=self.overlap_seconds
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_updated_checkpoint(path: Path) -> UpdatedReviewCheckpoint | None:
    value = read_json_checkpoint(path)
    if value is None:
        return None
    try:
        checkpoint = UpdatedReviewCheckpoint(**value)
    except TypeError as exc:
        raise CheckpointValidationError(
            "checkpoint fields do not match UpdatedReviewCheckpoint"
        ) from exc
    _validate(checkpoint)
    return checkpoint


def commit_updated_checkpoint(path: Path, checkpoint: UpdatedReviewCheckpoint) -> None:
    _validate(checkpoint)
    write_json_checkpoint(path, checkpoint.to_dict())


def build_updated_checkpoint(
    *,
    appid: int,
    high_watermark: datetime,
    overlap_seconds: int,
    committed_at: datetime,
    run_id: str,
) -> UpdatedReviewCheckpoint:
    checkpoint = UpdatedReviewCheckpoint(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        appid=appid,
        filter_value=UPDATED_STREAM_FILTER,
        high_watermark_at=_utc_iso(high_watermark),
        overlap_seconds=overlap_seconds,
        committed_at=_utc_iso(committed_at),
        run_id=run_id,
    )
    _validate(checkpoint)
    return checkpoint


def _validate(checkpoint: UpdatedReviewCheckpoint) -> None:
    if checkpoint.schema_version != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointValidationError("unsupported checkpoint schema version")
    if checkpoint.appid <= 0:
        raise CheckpointValidationError("checkpoint appid must be positive")
    if checkpoint.filter_value != UPDATED_STREAM_FILTER:
        raise CheckpointValidationError("checkpoint must represent filter=Updated")
    if not 0 <= checkpoint.overlap_seconds <= 86_400:
        raise CheckpointValidationError("overlap_seconds must be in [0, 86400]")
    if not checkpoint.run_id.strip():
        raise CheckpointValidationError("checkpoint run_id must not be empty")
    _parse_utc(checkpoint.high_watermark_at)
    _parse_utc(checkpoint.committed_at)


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CheckpointValidationError(
            "checkpoint timestamp must be ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CheckpointValidationError("checkpoint timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CheckpointValidationError("checkpoint datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
