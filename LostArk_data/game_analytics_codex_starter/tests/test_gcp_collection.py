from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from entertainment_platform.cloud.gcp import GcpClients, upload_immutable
from entertainment_platform.jobs import (
    collect_gcp_incremental,
    collect_gcp_review_backfill,
)


class NotFound(Exception):
    code = 404


class PreconditionFailed(Exception):
    code = 412


class FakeBlob:
    def __init__(self, store: dict[str, bytes], generations: dict[str, int], name: str):
        self.store = store
        self.generations = generations
        self.name = name
        self.generation: int | None = None

    def reload(self) -> None:
        if self.name not in self.store:
            raise NotFound()
        self.generation = self.generations[self.name]

    def download_to_filename(self, filename: str) -> None:
        Path(filename).write_bytes(self.store[self.name])

    def upload_from_filename(
        self, filename: str, *, if_generation_match: int | None = None
    ) -> None:
        self._put(Path(filename).read_bytes(), if_generation_match)

    def upload_from_string(
        self,
        data: str,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None:
        del content_type
        self._put(data.encode(), if_generation_match)

    def _put(self, data: bytes, expected: int | None) -> None:
        actual = self.generations.get(self.name, 0)
        if expected is not None and expected != actual:
            raise PreconditionFailed("generation mismatch")
        self.store[self.name] = data
        self.generations[self.name] = actual + 1
        self.generation = actual + 1

    def delete(self, *, if_generation_match: int | None = None) -> None:
        if self.generations.get(self.name) != if_generation_match:
            raise RuntimeError("generation mismatch")
        self.store.pop(self.name)
        self.generations.pop(self.name)


class FakeBucket:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.generations: dict[str, int] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.store, self.generations, name)


class FakeStorage:
    def __init__(self, bucket: FakeBucket):
        self._bucket = bucket

    def bucket(self, name: str) -> FakeBucket:
        assert name == "test-bucket"
        return self._bucket


class FakeJob:
    def result(self) -> None:
        return None


class FakeBigQuery:
    def __init__(self) -> None:
        self.loads: list[tuple[bytes, str, str]] = []

    def load_table_from_file(
        self,
        source_file: io.BufferedReader,
        destination: str,
        *,
        job_id: str,
        job_config: object,
    ) -> FakeJob:
        del job_config
        self.loads.append((source_file.read(), destination, job_id))
        return FakeJob()


def test_gcp_collection_persists_raw_state_manifest_and_replaces_table(
    monkeypatch,
) -> None:
    bucket = FakeBucket()
    bigquery = FakeBigQuery()
    clients = GcpClients(
        storage=FakeStorage(bucket),
        bigquery=bigquery,
        load_job_config_factory=lambda: object(),
    )

    def fake_run_source(**kwargs: object) -> dict[str, object]:
        output = kwargs["output"]
        raw_directory = kwargs["raw_directory"]
        assert isinstance(output, Path)
        assert isinstance(raw_directory, Path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"appid":1599340,"player_count":1}\n', encoding="utf-8")
        raw_directory.mkdir(parents=True, exist_ok=True)
        (raw_directory / "response.json").write_text("{}", encoding="utf-8")
        return {"complete": True, "rows_written": 1}

    monkeypatch.setattr(collect_gcp_incremental, "_run_source", fake_run_source)
    manifest = collect_gcp_incremental.run_gcp_collection(
        source="players",
        project_id="test-project",
        bucket_name="test-bucket",
        dataset="game_analytics",
        clients=clients,
        now=datetime(2026, 10, 23, tzinfo=UTC),
        execution_id="players/execution:1",
    )

    assert manifest["raw_objects_written"] == 1
    assert "locks/players.json" not in bucket.store
    assert "state/players/data.jsonl" in bucket.store
    assert "bronze/steam/players/players-execution-1/response.json" in bucket.store
    assert "manifests/players/players-execution-1.json" in bucket.store
    assert bigquery.loads[0][1] == "test-project.game_analytics.fact_player_snapshot"
    assert bigquery.loads[0][0].endswith(b"\n")


def test_gcp_collection_allows_byte_identical_execution_retry(monkeypatch) -> None:
    bucket = FakeBucket()
    bigquery = FakeBigQuery()
    clients = GcpClients(
        storage=FakeStorage(bucket),
        bigquery=bigquery,
        load_job_config_factory=lambda: object(),
    )

    def fake_run_source(**kwargs: object) -> dict[str, object]:
        output = kwargs["output"]
        raw_directory = kwargs["raw_directory"]
        assert isinstance(output, Path)
        assert isinstance(raw_directory, Path)
        output.write_text('{"appid":1599340,"player_count":1}\n', encoding="utf-8")
        raw_directory.mkdir(parents=True, exist_ok=True)
        (raw_directory / "response.json").write_text("{}", encoding="utf-8")
        return {"complete": True, "rows_written": 1}

    monkeypatch.setattr(collect_gcp_incremental, "_run_source", fake_run_source)
    arguments = {
        "source": "players",
        "project_id": "test-project",
        "bucket_name": "test-bucket",
        "dataset": "game_analytics",
        "clients": clients,
        "now": datetime(2026, 10, 23, tzinfo=UTC),
        "execution_id": "players-retry-1",
    }

    first = collect_gcp_incremental.run_gcp_collection(**arguments)
    second = collect_gcp_incremental.run_gcp_collection(**arguments)

    assert first == second
    assert len(bigquery.loads) == 2
    assert "locks/players.json" not in bucket.store


def test_gcp_collection_persists_valid_empty_state(monkeypatch) -> None:
    bucket = FakeBucket()
    bigquery = FakeBigQuery()
    clients = GcpClients(
        storage=FakeStorage(bucket),
        bigquery=bigquery,
        load_job_config_factory=lambda: object(),
    )

    def fake_empty_run(**kwargs: object) -> dict[str, object]:
        output = kwargs["output"]
        assert isinstance(output, Path)
        assert output.exists()
        return {"complete": True, "rows_written": 0}

    monkeypatch.setattr(collect_gcp_incremental, "_run_source", fake_empty_run)
    manifest = collect_gcp_incremental.run_gcp_collection(
        source="news",
        project_id="test-project",
        bucket_name="test-bucket",
        dataset="game_analytics",
        clients=clients,
        now=datetime(2026, 10, 23, tzinfo=UTC),
        execution_id="news-empty-1",
    )

    assert manifest["state_bytes"] == 0
    assert bucket.store["state/news/data.jsonl"] == b""
    assert bigquery.loads == []
    assert "locks/news.json" not in bucket.store


def test_immutable_upload_rejects_same_name_with_different_bytes(
    tmp_path: Path,
) -> None:
    bucket = FakeBucket()
    source = tmp_path / "response.json"
    source.write_text('{"version":1}', encoding="utf-8")
    upload_immutable(bucket, "bronze/response.json", source)

    source.write_text('{"version":2}', encoding="utf-8")
    try:
        upload_immutable(bucket, "bronze/response.json", source)
    except FileExistsError as exc:
        assert "different bytes" in str(exc)
    else:
        raise AssertionError("an immutable object collision must not overwrite data")

    assert bucket.store["bronze/response.json"] == b'{"version":1}'


def test_reviews_requires_a_bootstrap_checkpoint(monkeypatch) -> None:
    bucket = FakeBucket()
    clients = GcpClients(
        storage=FakeStorage(bucket),
        bigquery=FakeBigQuery(),
        load_job_config_factory=lambda: object(),
    )
    monkeypatch.setattr(
        collect_gcp_incremental,
        "run_incremental",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    try:
        collect_gcp_incremental.run_gcp_collection(
            source="reviews",
            project_id="test-project",
            bucket_name="test-bucket",
            dataset="game_analytics",
            clients=clients,
            now=datetime(2026, 10, 23, tzinfo=UTC),
            execution_id="reviews-1",
        )
    except RuntimeError as exc:
        assert "bootstrap checkpoint" in str(exc)
    else:
        raise AssertionError("missing checkpoint must fail")
    assert "locks/reviews.json" not in bucket.store


def test_due_sources_uses_last_success_markers(tmp_path: Path) -> None:
    bucket = FakeBucket()
    now = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)
    bucket.store["state/players/last_success.json"] = (
        b'{"completed_at":"2026-10-23T02:20:00Z"}'
    )
    bucket.generations["state/players/last_success.json"] = 1
    bucket.store["state/reviews/last_success.json"] = (
        b'{"completed_at":"2026-10-22T21:00:00Z"}'
    )
    bucket.generations["state/reviews/last_success.json"] = 1

    assert collect_gcp_incremental.due_sources(
        bucket=bucket, now=now, root=tmp_path
    ) == ["reviews", "news"]


def test_monthly_partitions_are_inclusive_without_gaps() -> None:
    partitions = collect_gcp_review_backfill.monthly_partitions(
        datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        datetime(2026, 3, 2, 3, 4, 5, tzinfo=UTC),
    )

    assert partitions == (
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
        ),
        (
            datetime(2026, 2, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 3, 2, 3, 4, 5, tzinfo=UTC),
        ),
    )


def test_one_shot_gcp_backfill_persists_partitions_and_loads_combined_table() -> None:
    bucket = FakeBucket()
    bigquery = FakeBigQuery()
    clients = GcpClients(
        storage=FakeStorage(bucket),
        bigquery=bigquery,
        load_job_config_factory=lambda: object(),
    )

    def fake_backfill(**kwargs: object) -> dict[str, object]:
        range_start = kwargs["range_start"]
        history_path = kwargs["history_path"]
        checkpoint_path = kwargs["checkpoint_path"]
        raw_directory = kwargs["raw_directory"]
        run_id = kwargs["run_id"]
        assert isinstance(range_start, datetime)
        assert isinstance(history_path, Path)
        assert isinstance(checkpoint_path, Path)
        assert isinstance(raw_directory, Path)
        assert isinstance(run_id, str)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps(
                {
                    "appid": 1599340,
                    "recommendationid": f"{range_start:%Y%m}",
                    "updated_at": f"{range_start:%Y-%m-%dT%H:%M:%SZ}",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        checkpoint_path.write_text('{"completed":true}\n', encoding="utf-8")
        raw_directory.mkdir(parents=True, exist_ok=True)
        (raw_directory / f"{run_id}.json").write_text("{}", encoding="utf-8")
        return {
            "pages_fetched_this_run": 2,
            "pages_committed_total": 2,
            "rows_observed_total": 1,
            "unique_reviews_stored": 1,
            "total_matching_reported": 1,
            "duplicate_versions_in_storage": 0,
            "out_of_range_rows": 0,
            "invalid_timestamp_rows": 0,
            "completed": True,
        }

    manifest = collect_gcp_review_backfill.run_gcp_review_backfill(
        project_id="test-project",
        bucket_name="test-bucket",
        dataset="game_analytics",
        range_start=datetime(2026, 1, 1, tzinfo=UTC),
        range_end=datetime(2026, 2, 2, tzinfo=UTC),
        clients=clients,
        execution_id="review/backfill:1",
        request_interval_seconds=0,
        backfill_runner=fake_backfill,
        now=datetime(2026, 10, 24, tzinfo=UTC),
    )

    assert manifest["partition_count"] == 2
    assert manifest["row_count"] == 2
    assert manifest["expected_rows_from_api"] == 2
    assert manifest["api_unreturned_rows"] == 0
    assert "locks/review_backfill.json" not in bucket.store
    assert "state/review_backfill/spec.json" in bucket.store
    assert "state/review_backfill/partitions/2026-01/data.jsonl" in bucket.store
    assert "state/review_backfill/partitions/2026-02/checkpoint.json" in bucket.store
    assert "manifests/review_backfill/review-backfill-1.json" in bucket.store
    assert bigquery.loads[0][1] == "test-project.game_analytics.silver_review_history"
    assert len(bigquery.loads[0][0].splitlines()) == 2


def test_completed_partition_records_api_reported_but_unreturned_rows() -> None:
    quality = collect_gcp_review_backfill._validate_completed_partition(
        {
            "rows_observed_total": 23_930,
            "unique_reviews_stored": 23_930,
            "total_matching_reported": 23_948,
            "duplicate_versions_in_storage": 0,
            "out_of_range_rows": 0,
            "invalid_timestamp_rows": 0,
        }
    )

    assert quality == {"api_unreturned_rows": 18}
