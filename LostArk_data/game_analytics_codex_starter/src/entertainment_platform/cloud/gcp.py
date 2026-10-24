"""Small GCS and BigQuery adapter used by ephemeral collector jobs.

Imports of Google client libraries stay inside ``clients_from_adc`` so the
offline test suite remains dependency-light.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class Blob(Protocol):
    generation: int | None

    def download_to_filename(self, filename: str) -> None: ...

    def upload_from_filename(
        self, filename: str, *, if_generation_match: int | None = None
    ) -> None: ...

    def upload_from_string(
        self,
        data: str,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None: ...

    def delete(self, *, if_generation_match: int | None = None) -> None: ...

    def reload(self) -> None: ...


class Bucket(Protocol):
    def blob(self, name: str) -> Blob: ...


class StorageClient(Protocol):
    def bucket(self, name: str) -> Bucket: ...


class BigQueryClient(Protocol):
    def load_table_from_file(
        self, source_file: Any, destination: str, *, job_id: str, job_config: Any
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class GcpClients:
    storage: StorageClient
    bigquery: BigQueryClient
    load_job_config_factory: Any


def clients_from_adc(project_id: str) -> GcpClients:
    """Build Application Default Credential clients at the runtime boundary."""

    from google.cloud import bigquery, storage

    return GcpClients(
        storage=storage.Client(project=project_id),
        bigquery=bigquery.Client(project=project_id),
        load_job_config_factory=lambda: bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )


def download_if_present(bucket: Bucket, object_name: str, destination: Path) -> bool:
    blob = bucket.blob(object_name)
    try:
        blob.reload()
    except Exception as exc:  # Google NotFound is optional in offline tests.
        if getattr(exc, "code", None) == 404:
            return False
        raise
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob.download_to_filename(str(destination))
    return True


def upload_state(bucket: Bucket, object_name: str, source: Path) -> None:
    """Replace one state object using an observed-generation precondition."""

    blob = bucket.blob(object_name)
    expected_generation = 0
    try:
        blob.reload()
        expected_generation = int(blob.generation or 0)
    except Exception as exc:
        if getattr(exc, "code", None) != 404:
            raise
    blob.upload_from_filename(str(source), if_generation_match=expected_generation)


def upload_immutable(bucket: Bucket, object_name: str, source: Path) -> None:
    """Create an immutable object, accepting only byte-identical retries.

    A Cloud Run execution can be retried with the same execution identifier
    after some Raw objects have already reached GCS. The create-only
    precondition still protects against overwrite; on a collision we download
    the existing object and accept it only when its bytes match the local
    source exactly.
    """

    blob = bucket.blob(object_name)
    try:
        blob.upload_from_filename(str(source), if_generation_match=0)
        return
    except Exception as exc:
        if getattr(exc, "code", None) != 412:
            raise
        conflict = exc

    with tempfile.NamedTemporaryFile(prefix="gcs-existing-", delete=False) as handle:
        existing_path = Path(handle.name)
    try:
        blob.download_to_filename(str(existing_path))
        if existing_path.read_bytes() != source.read_bytes():
            raise FileExistsError(
                f"immutable GCS object already exists with different bytes: {object_name}"
            ) from conflict
    finally:
        existing_path.unlink(missing_ok=True)


def acquire_lock(bucket: Bucket, object_name: str, payload: dict[str, object]) -> int:
    blob = bucket.blob(object_name)
    blob.upload_from_string(
        json.dumps(payload, sort_keys=True),
        content_type="application/json",
        if_generation_match=0,
    )
    blob.reload()
    if blob.generation is None:
        raise RuntimeError("GCS lock generation was not returned")
    return int(blob.generation)


def release_lock(bucket: Bucket, object_name: str, generation: int) -> None:
    bucket.blob(object_name).delete(if_generation_match=generation)


def replace_jsonl_table(
    clients: GcpClients,
    *,
    source: Path,
    destination: str,
) -> str | None:
    """Atomically replace a small Silver table from its canonical JSONL state."""

    if not source.exists() or source.stat().st_size == 0:
        return None
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    table_token = destination.rsplit(".", 1)[-1].replace("_", "-")
    job_id = f"state-{table_token}-{digest[:24]}"
    with source.open("rb") as handle:
        job = clients.bigquery.load_table_from_file(
            handle,
            destination,
            job_id=job_id,
            job_config=clients.load_job_config_factory(),
        )
        job.result()
    return job_id
