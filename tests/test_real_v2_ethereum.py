import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aave_bns.evm_rpc import RpcError
from aave_bns.real_v2_ethereum import (
    BlockChunk,
    address_concentration,
    build_chunks,
    build_reserve_week_action_panel,
    build_weekly_action_panel,
    fetch_log_chunks,
    load_ethereum_config,
    project_relative_path,
    read_and_validate_boundary_cache,
    read_cohort_calendar,
    resolve_boundaries,
    safe_rpc_endpoint,
    source_revision,
    write_csv_records,
)


def record(
    action: str,
    week: int,
    tx_suffix: str,
    actor: str,
    beneficiary: str,
    reserve: str,
    amount: int,
):
    return {
        "event_week": week,
        "action": action,
        "tx_hash": "0x" + tx_suffix * 64,
        "actor_address": actor,
        "beneficiary_address": beneficiary,
        "counterparty_address": "",
        "reserve_address": reserve,
        "amount_raw": str(amount),
        "secondary_amount_raw": "",
    }


def test_config_topics_match_pinned_decoder():
    config = load_ethereum_config("configs/real_v2_ethereum.yaml")
    assert config["pool"]["address"].lower() == ("0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2")
    assert len(config["events"]) == 5


def test_locked_ethereum_calendar_has_33_observation_weeks():
    rows = read_cohort_calendar(
        "data/metadata/real_v2_event_week_calendar.csv",
        cohort_id="ethereum_gho",
        minimum_event_week=-16,
        maximum_event_week=16,
    )
    assert len(rows) == 33
    assert rows[16]["event_week"] == "0"
    assert rows[16]["window_start_utc"] == "2023-07-15T14:02:59Z"


def test_chunks_are_contiguous_and_bounded():
    chunks = build_chunks(100, 125, 10)
    assert [(chunk.from_block, chunk.to_block) for chunk in chunks] == [
        (100, 109),
        (110, 119),
        (120, 125),
    ]


class RangeLimitedLogClient:
    def __init__(self, pool_address: str, topic: str, *, maximum_width: int):
        self.pool_address = pool_address
        self.topic = topic
        self.maximum_width = maximum_width
        self.requests: list[tuple[int, int]] = []

    def logs(self, filter_parameters):
        start = int(filter_parameters["fromBlock"], 16)
        end = int(filter_parameters["toBlock"], 16)
        self.requests.append((start, end))
        if end - start + 1 > self.maximum_width:
            raise RpcError("eth_getLogs HTTP 400: Log response size exceeded")
        return [
            {
                "address": self.pool_address,
                "topics": [self.topic],
                "blockNumber": hex(start),
                "blockHash": "0x" + f"{start:064x}",
                "transactionHash": "0x" + f"{start + 1:064x}",
                "transactionIndex": "0x0",
                "logIndex": "0x0",
                "data": "0x",
                "removed": False,
            }
        ]


def test_log_fetch_bisects_provider_limited_range_without_dropping_blocks(tmp_path):
    pool = "0x" + "1" * 40
    topic = "0x" + "2" * 64
    client = RangeLimitedLogClient(pool, topic, maximum_width=2)
    chunks = [BlockChunk(100, 103)]
    logs, records = fetch_log_chunks(
        client,
        chunks,
        pool_address=pool,
        topics=[topic],
        chunk_directory=tmp_path / "raw",
        project_root=tmp_path,
        workers=1,
        resume=True,
        progress_every=1,
    )
    assert client.requests == [(100, 103), (100, 101), (102, 103)]
    assert [int(log["blockNumber"], 16) for log in logs] == [100, 102]
    assert records[0]["from_block"] == 100
    assert records[0]["to_block"] == 103
    assert (tmp_path / "raw" / chunks[0].name).is_file()


def test_log_fetch_starts_with_smaller_queries_and_bounds_pending_work(tmp_path):
    pool = "0x" + "1" * 40
    topic = "0x" + "2" * 64
    client = RangeLimitedLogClient(pool, topic, maximum_width=2)
    chunks = [BlockChunk(100, 107)]
    logs, records = fetch_log_chunks(
        client,
        chunks,
        pool_address=pool,
        topics=[topic],
        chunk_directory=tmp_path / "raw",
        project_root=tmp_path,
        workers=1,
        resume=True,
        progress_every=1,
        progress_interval_seconds=1,
        initial_query_width=2,
        maximum_pending=1,
    )
    assert client.requests == [(100, 101), (102, 103), (104, 105), (106, 107)]
    assert [int(log["blockNumber"], 16) for log in logs] == [100, 102, 104, 106]
    assert records[0]["block_count"] == 8


def test_log_fetch_rejects_invalid_concurrency_settings(tmp_path):
    with pytest.raises(ValueError, match="maximum_pending"):
        fetch_log_chunks(
            RangeLimitedLogClient("0x" + "1" * 40, "0x" + "2" * 64, maximum_width=2),
            [BlockChunk(100, 101)],
            pool_address="0x" + "1" * 40,
            topics=["0x" + "2" * 64],
            chunk_directory=tmp_path / "raw",
            project_root=tmp_path,
            workers=2,
            resume=True,
            maximum_pending=1,
        )


def test_log_fetch_time_slice_preserves_completed_atomic_chunks(tmp_path):
    pool = "0x" + "1" * 40
    topic = "0x" + "2" * 64

    class SlowClient(RangeLimitedLogClient):
        def logs(self, filter_parameters):
            time.sleep(0.03)
            return super().logs(filter_parameters)

    chunks = [BlockChunk(100, 101), BlockChunk(102, 103)]
    with pytest.raises(RuntimeError, match="time slice expired"):
        fetch_log_chunks(
            SlowClient(pool, topic, maximum_width=2),
            chunks,
            pool_address=pool,
            topics=[topic],
            chunk_directory=tmp_path / "raw",
            project_root=tmp_path,
            workers=1,
            resume=True,
            progress_every=1,
            progress_interval_seconds=1,
            maximum_pending=1,
            maximum_runtime_seconds=0.01,
        )
    assert sum(path.is_file() for path in (tmp_path / "raw").glob("*.jsonl.gz")) == 1


def test_log_fetch_does_not_split_rate_limits(tmp_path):
    class RateLimitedClient:
        requests = 0

        def logs(self, _filter_parameters):
            self.requests += 1
            raise RpcError("eth_getLogs HTTP 429: rate limit exceeded")

    client = RateLimitedClient()
    with pytest.raises(RpcError, match="rate limit"):
        fetch_log_chunks(
            client,
            [BlockChunk(100, 103)],
            pool_address="0x" + "1" * 40,
            topics=["0x" + "2" * 64],
            chunk_directory=tmp_path / "raw",
            project_root=tmp_path,
            workers=1,
            resume=True,
            progress_every=1,
        )
    assert client.requests == 1


def test_log_fetch_stops_bisecting_at_the_configured_minimum(tmp_path):
    class AlwaysBadRequestClient:
        def __init__(self):
            self.requests = []

        def logs(self, filter_parameters):
            self.requests.append(
                (
                    int(filter_parameters["fromBlock"], 16),
                    int(filter_parameters["toBlock"], 16),
                )
            )
            raise RpcError("eth_getLogs HTTP 400")

    client = AlwaysBadRequestClient()
    with pytest.raises(RpcError, match="HTTP 400"):
        fetch_log_chunks(
            client,
            [BlockChunk(100, 107)],
            pool_address="0x" + "1" * 40,
            topics=["0x" + "2" * 64],
            chunk_directory=tmp_path / "raw",
            project_root=tmp_path,
            workers=1,
            resume=True,
            progress_every=1,
            minimum_query_width=2,
        )
    assert client.requests == [(100, 107), (100, 103), (100, 101)]


class BoundaryClient:
    def __init__(self, timestamps: list[int]):
        self.timestamps = timestamps

    def latest_block_number(self) -> int:
        return len(self.timestamps) - 1

    def block(self, block_number: int):
        return {
            "number": hex(block_number),
            "timestamp": hex(self.timestamps[block_number]),
            "hash": "0x" + f"{block_number:064x}",
        }


def test_l2_activation_boundary_uses_exact_execution_block_with_same_second_predecessors(
    tmp_path,
):
    client = BoundaryClient([100, 110, 120, 120, 120, 130, 140])
    targets = [
        (-1, datetime.fromtimestamp(110, tz=timezone.utc)),
        (0, datetime.fromtimestamp(120, tz=timezone.utc)),
        (1, datetime.fromtimestamp(130, tz=timezone.utc)),
    ]
    rows = resolve_boundaries(
        client,
        targets,
        activation_block=4,
        activation_utc=targets[1][1],
        workers=2,
        seed_seconds_per_block=10,
        initial_radius_blocks=2,
    )
    assert [row["start_block"] for row in rows] == [1, 4, 5]
    assert rows[1]["previous_block_timestamp"] == "1970-01-01T00:02:00Z"

    cache = write_csv_records(tmp_path / "boundaries.csv", rows)
    assert read_and_validate_boundary_cache(cache, targets) == [
        {key: str(value) for key, value in row.items()} for row in rows
    ]


def test_concentration_counts_one_address_once_per_event():
    address_a = "0x" + "1" * 40
    address_b = "0x" + "2" * 40
    rows = [
        record("supply", 0, "a", address_a, address_a, "0x" + "3" * 40, 5),
        record("supply", 0, "b", address_a, address_b, "0x" + "3" * 40, 7),
    ]
    metrics = address_concentration(rows)
    assert metrics["active_addresses"] == 2
    assert metrics["address_event_incidences"] == 3
    assert metrics["effective_active_addresses"] == pytest.approx(1.8)


def test_panels_never_sum_amounts_across_reserves():
    calendar = [
        {
            "cohort_id": "ethereum_gho",
            "chain_id": "1",
            "event_week": "0",
            "window_start_utc": "2023-07-15T14:02:59Z",
            "window_end_utc_exclusive": "2023-07-22T14:02:59Z",
        }
    ]
    address_a = "0x" + "1" * 40
    address_b = "0x" + "2" * 40
    reserve_a = "0x" + "3" * 40
    reserve_b = "0x" + "4" * 40
    rows = [
        record("supply", 0, "a", address_a, address_a, reserve_a, 5),
        record("supply", 0, "b", address_b, address_b, reserve_b, 700),
    ]
    weekly = build_weekly_action_panel(rows, calendar)
    supply = next(row for row in weekly if row["action"] == "supply")
    assert supply["event_count"] == 2
    assert "total_amount_raw" not in supply
    reserve_rows = build_reserve_week_action_panel(rows, calendar)
    assert {row["total_amount_raw"] for row in reserve_rows} == {"5", "700"}
    assert all(row["amount_unit"] == "reserve_native_integer" for row in reserve_rows)


def test_rpc_endpoint_label_drops_paths_and_credentials():
    assert safe_rpc_endpoint("https://secret@example.org/private/key?x=1") == (
        "https://example.org"
    )


def test_project_relative_path_is_portable_and_confined(tmp_path):
    project = tmp_path / "clone"
    chunk = project / "data" / "raw" / "chunk.jsonl.gz"
    chunk.parent.mkdir(parents=True)
    chunk.touch()
    assert project_relative_path(chunk, project) == "data/raw/chunk.jsonl.gz"
    with pytest.raises(ValueError, match="outside the project root"):
        project_relative_path(tmp_path / "elsewhere.json", project)


def test_source_revision_accepts_a_pinned_environment_override(monkeypatch):
    revision = "a" * 40
    monkeypatch.setenv("AAVE_BNS_SOURCE_REVISION", revision)
    assert source_revision(".") == revision
    monkeypatch.setenv("AAVE_BNS_SOURCE_REVISION", "short")
    with pytest.raises(ValueError, match="40-character Git SHA"):
        source_revision(".")


def test_source_files_are_present():
    for path in [
        "src/aave_bns/evm_rpc.py",
        "src/aave_bns/aave_v3_events.py",
        "src/aave_bns/real_v2_ethereum.py",
        "scripts/run_real_v2_ethereum.py",
    ]:
        assert Path(path).is_file()
