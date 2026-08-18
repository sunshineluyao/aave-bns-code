from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest
import requests

import aave_bns.real_v5_arbitrum as real_v5
from aave_bns.evm_rpc import RpcError, canonical_json_sha256
from aave_bns.real_v5_arbitrum import (
    _bulk_log_runtime,
    _BulkLogSelection,
    _cached_bulk_log_selection,
    _canonical_json_list_sha256,
    _complete_chunk_cache,
    _historical_contract_code_check,
    _independent_endpoint,
    _PacedLogClient,
    _RedactingRpcClient,
    _required_rpc_url,
    _select_bulk_log_source,
    beneficiary_metrics,
    build_comparable_beneficiary_panel,
    build_weekly_beneficiary_panel,
    cross_provider_consensus_checks,
    load_real_v5_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _calendar(cohort_id: str, chain_id: int) -> list[dict[str, object]]:
    return [
        {
            "cohort_id": cohort_id,
            "chain_id": chain_id,
            "event_week": -1,
            "window_start_utc": "2024-01-01T00:00:00Z",
            "window_end_utc_exclusive": "2024-01-08T00:00:00Z",
        },
        {
            "cohort_id": cohort_id,
            "chain_id": chain_id,
            "event_week": 0,
            "window_start_utc": "2024-01-08T00:00:00Z",
            "window_end_utc_exclusive": "2024-01-15T00:00:00Z",
        },
    ]


def _raw_log(
    *,
    block_number: int = 100,
    data: str = "0x01",
    include_block_timestamp: bool = False,
) -> dict[str, object]:
    record: dict[str, object] = {
        "address": "0x" + "1" * 40,
        "topics": ["0x" + "2" * 64],
        "data": data,
        "blockHash": "0x" + f"{block_number:064x}",
        "blockNumber": hex(block_number),
        "transactionHash": "0x" + f"{block_number:064x}",
        "transactionIndex": "0x0",
        "logIndex": "0x1",
        "removed": False,
    }
    if include_block_timestamp:
        record["blockTimestamp"] = "0x65aa0000"
    return record


class _FixedLogClient:
    def __init__(self, logs):
        self._logs = logs

    def logs(self, _parameters):
        return self._logs


def test_streaming_canonical_list_hash_is_byte_equivalent_to_existing_hash():
    records = [_raw_log(), _raw_log(block_number=101, include_block_timestamp=True)]
    assert _canonical_json_list_sha256([]) == canonical_json_sha256([])
    assert _canonical_json_list_sha256(records) == canonical_json_sha256(records)


def test_real_v5_config_pins_arbitrum_pool_gho_and_single_secret():
    config = load_real_v5_config(ROOT / "configs/real_v5_arbitrum.yaml")
    assert config["chain"]["chain_id"] == 42161
    assert config["chain"]["primary_rpc"]["environment_variable"] == "ARBITRUM_RPC_URL"
    assert config["pool"]["address"].lower() == "0x794a61358d6845594f94dc1db02a252b5b4814ad"
    assert config["gho"]["underlying_address"].lower() == (
        "0x7dff72693f6a4149b17e7c6314655f6a9f7c8b33"
    )
    assert config["release_gate"]["causal_estimates_allowed"] is False
    assert config["retrieval"]["initial_blocks_per_log_query"] == 10000
    assert config["retrieval"]["minimum_adaptive_blocks_per_log_query"] == 10
    assert config["retrieval"]["minimum_viable_bulk_blocks_per_query"] == 2000
    assert config["retrieval"]["bulk_log_provider_order"] == [
        "validation_rpc",
        "primary_rpc",
    ]
    assert config["retrieval"]["log_workers"] == 4
    assert config["retrieval"]["maximum_pending_log_chunks"] == 8
    assert config["retrieval"]["maximum_runtime_seconds"] == 9000


def test_required_rpc_secret_fails_without_revealing_any_value(monkeypatch):
    monkeypatch.delenv("ARBITRUM_RPC_URL", raising=False)
    with pytest.raises(ValueError, match="ARBITRUM_RPC_URL"):
        _required_rpc_url({"environment_variable": "ARBITRUM_RPC_URL"})


def test_required_rpc_secret_rejects_an_api_key_without_a_full_url(monkeypatch):
    monkeypatch.setenv("ARBITRUM_RPC_URL", "key-only-value")
    with pytest.raises(ValueError, match="complete http\\(s\\) RPC URL"):
        _required_rpc_url({"environment_variable": "ARBITRUM_RPC_URL"})


def test_independence_requires_different_sanitized_endpoints():
    assert _independent_endpoint(
        "https://secret@alchemy.example/v2/key-one",
        "https://arb1.arbitrum.io/rpc",
    )


def test_rpc_failures_redact_secret_url_from_exception_text():
    def fail(*args, **kwargs):
        raise requests.RequestException("network unavailable")

    client = _RedactingRpcClient(
        "https://rpc.example/v2/super-secret-key",
        maximum_attempts=1,
        post=fail,
    )
    with pytest.raises(Exception) as error:
        client.chain_id()
    assert "super-secret-key" not in str(error.value)
    assert "https://rpc.example" in str(error.value)
    assert not _independent_endpoint(
        "https://alchemy.example/v2/key-one",
        "https://alchemy.example/v2/key-two",
    )


def test_primary_log_range_errors_fail_after_one_probe_before_bisection():
    calls = 0

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "jsonrpc": "2.0",
                "id": calls,
                "error": {"code": -32005, "message": "block range is too wide"},
            }

    def post(_url, *, json, timeout):
        nonlocal calls
        calls += 1
        return Response()

    client = _RedactingRpcClient(
        "https://rpc.example/v2/super-secret-key",
        maximum_attempts=6,
        post=post,
    )
    with pytest.raises(RpcError, match="block range is too wide"):
        client.logs({"fromBlock": "0x1", "toBlock": "0x2"})
    assert calls == 1


def test_bulk_log_probe_selects_first_provider_with_a_viable_range():
    class WidthClient:
        def __init__(self, maximum_width):
            self.maximum_width = maximum_width
            self.requests = []

        def logs(self, parameters):
            start = int(parameters["fromBlock"], 16)
            end = int(parameters["toBlock"], 16)
            width = end - start + 1
            self.requests.append(width)
            if width > self.maximum_width:
                raise RpcError("eth_getLogs HTTP 400: block range is too wide")
            return []

    primary = WidthClient(maximum_width=1)
    validation = WidthClient(maximum_width=4)
    selection = _select_bulk_log_source(
        chain={
            "primary_rpc": {"source_id": "protected"},
            "validation_rpc": {"source_id": "public"},
        },
        retrieval={
            "minimum_viable_bulk_blocks_per_query": 2,
            "bulk_log_probe_widths": [4, 2],
            "bulk_log_provider_order": ["validation_rpc", "primary_rpc"],
        },
        primary=primary,
        validation=validation,
        pool_address="0x" + "1" * 40,
        topics=["0x" + "2" * 64],
        probe_start_block=100,
        maximum_block=110,
    )
    assert selection.role == "validation_rpc"
    assert selection.source_id == "public"
    assert selection.crosscheck_role == "primary_rpc"
    assert selection.query_width == 4
    assert validation.requests == [4]
    assert primary.requests == []


def test_bulk_log_probe_refuses_millions_of_tiny_queries():
    class TenBlockOnlyClient:
        def logs(self, _parameters):
            raise RpcError("eth_getLogs HTTP 400: block range is too wide")

    with pytest.raises(RuntimeError, match="minimum viable bulk"):
        _select_bulk_log_source(
            chain={
                "primary_rpc": {"source_id": "protected"},
                "validation_rpc": {"source_id": "public"},
            },
            retrieval={
                "minimum_viable_bulk_blocks_per_query": 2,
                "bulk_log_probe_widths": [4, 2],
                "bulk_log_provider_order": ["validation_rpc", "primary_rpc"],
            },
            primary=TenBlockOnlyClient(),
            validation=TenBlockOnlyClient(),
            pool_address="0x" + "1" * 40,
            topics=["0x" + "2" * 64],
            probe_start_block=100,
            maximum_block=110,
        )


def test_complete_chunk_cache_requires_every_expected_atomic_file(tmp_path):
    chunks = [real_v5.BlockChunk(100, 109), real_v5.BlockChunk(110, 119)]
    assert _complete_chunk_cache(chunks, tmp_path) is False
    (tmp_path / chunks[0].name).touch()
    assert _complete_chunk_cache(chunks, tmp_path) is False
    (tmp_path / chunks[1].name).touch()
    assert _complete_chunk_cache(chunks, tmp_path) is True
    assert _complete_chunk_cache([], tmp_path) is False


def test_complete_cache_selection_skips_bulk_clients_and_uses_verifier_for_samples(
    monkeypatch,
):
    primary = object()
    verifier = object()
    monkeypatch.setenv("ARBITRUM_VERIFY_RPC_URL", "https://archive.example/v2/secret")
    selection = _cached_bulk_log_selection(
        chain={
            "validation_rpc": {
                "source_id": "arbitrum_official_rpc",
                "environment_variable": "ARBITRUM_VERIFY_RPC_URL",
            }
        },
        retrieval={"initial_blocks_per_log_query": 10_000},
        cache_client=primary,
        validation_client=verifier,
    )
    assert selection.role == "validated_cache"
    assert selection.source_id == "arbitrum_official_rpc"
    assert selection.client is primary
    assert selection.crosscheck_client is verifier
    assert selection.crosscheck_source_id == "arbitrum_user_configured_verifier"
    assert selection.query_width == 10_000


def test_paced_log_client_spaces_request_starts_without_changing_results():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    class Client:
        def __init__(self, clock):
            self.clock = clock
            self.starts = []

        def logs(self, parameters):
            self.starts.append(self.clock())
            return [parameters]

    clock = Clock()
    client = Client(clock)
    paced = _PacedLogClient(
        client,
        minimum_interval_seconds=1.0,
        clock=clock,
        sleep=clock.sleep,
    )
    assert paced.logs({"id": 1}) == [{"id": 1}]
    assert paced.logs({"id": 2}) == [{"id": 2}]
    assert client.starts == [0.0, 1.0]


def test_paced_log_client_does_not_hold_the_shared_lock_while_sleeping():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    class Client:
        def logs(self, parameters):
            return [parameters]

    clock = Clock()
    lock_was_available = []
    paced = None

    def sleep(seconds):
        assert paced is not None
        acquired = paced._lock.acquire(blocking=False)
        lock_was_available.append(acquired)
        if acquired:
            paced._lock.release()
        clock.now += seconds

    paced = _PacedLogClient(
        Client(),
        minimum_interval_seconds=1.0,
        clock=clock,
        sleep=sleep,
    )
    assert paced.logs({"id": 1}) == [{"id": 1}]
    assert paced.logs({"id": 2}) == [{"id": 2}]
    assert lock_was_available == [True]


def test_paced_log_client_shares_one_cooldown_and_retries_the_same_request():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    class Client:
        def __init__(self, clock):
            self.clock = clock
            self.starts = []

        def logs_once(self, parameters):
            self.starts.append(self.clock())
            if len(self.starts) == 1:
                raise RpcError("eth_getLogs HTTP 429: Too Many Requests")
            return [parameters]

        def logs(self, _parameters):
            raise AssertionError("paced sources must bypass generic burst retries")

    clock = Clock()
    client = Client(clock)
    paced = _PacedLogClient(
        client,
        minimum_interval_seconds=1.0,
        rate_limit_cooldown_seconds=65.0,
        clock=clock,
        sleep=clock.sleep,
    )
    request = {"fromBlock": "0x1", "toBlock": "0x2"}
    assert paced.logs(request) == [request]
    assert client.starts == [0.0, 65.0]

    clock.now = 100.0
    assert paced._start_shared_cooldown() is True
    assert paced._start_shared_cooldown() is False
    assert paced._cooldown_until == 165.0


def test_paced_log_client_retries_transient_503_without_range_bisection():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    class Client:
        def __init__(self, clock):
            self.clock = clock
            self.starts = []

        def logs_once(self, parameters):
            self.starts.append(self.clock())
            if len(self.starts) == 1:
                raise RpcError("eth_getLogs failed after 1 attempts: 503 Service Unavailable")
            return [parameters]

        def logs(self, _parameters):
            raise AssertionError("paced sources must bypass generic burst retries")

    clock = Clock()
    client = Client(clock)
    paced = _PacedLogClient(
        client,
        minimum_interval_seconds=1.0,
        clock=clock,
        sleep=clock.sleep,
    )
    request = {"fromBlock": "0x1", "toBlock": "0x2"}
    assert paced.logs(request) == [request]
    assert client.starts == [0.0, 5.0]


def test_official_public_bulk_runtime_is_paced_but_other_sources_are_not():
    client = object()
    common = {
        "role": "validation_rpc",
        "client": client,
        "crosscheck_role": "primary_rpc",
        "crosscheck_source_id": "protected",
        "crosscheck_client": object(),
        "query_width": 10_000,
    }
    public = _BulkLogSelection(source_id="arbitrum_official_rpc", **common)
    runtime = _bulk_log_runtime(
        public, {"log_workers": 4, "maximum_pending_log_chunks": 8}
    )
    assert isinstance(runtime.client, _PacedLogClient)
    assert runtime.workers == 2
    assert runtime.maximum_pending == 4
    assert runtime.minimum_interval_seconds == 1.0
    assert runtime.rate_limit_cooldown_seconds == 65.0

    paid = _BulkLogSelection(source_id="protected", **common)
    unpaced = _bulk_log_runtime(
        paid, {"log_workers": 4, "maximum_pending_log_chunks": 8}
    )
    assert unpaced.client is client
    assert unpaced.workers == 4
    assert unpaced.maximum_pending == 8
    assert unpaced.minimum_interval_seconds == 0.0
    assert unpaced.rate_limit_cooldown_seconds == 0.0


def test_non_archive_validation_rpc_keeps_historical_code_gate_pending():
    class CodeClient:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error

        def code(self, address, block_number):
            if self.error:
                raise self.error
            return self.result

    primary = CodeClient(result="0x6001")
    validation = CodeClient(error=RpcError("historical state unavailable"))
    primary_code, validation_code, exact_match, status, diagnostic = (
        _historical_contract_code_check(
            primary,
            validation,
            address="0x" + "1" * 40,
            block_number=228027379,
            endpoints_independent=True,
        )
    )
    assert primary_code == "0x6001"
    assert validation_code == ""
    assert exact_match is False
    assert status == "validation_historical_state_unavailable"
    assert diagnostic == {
        "error_type": "RpcError",
        "redacted_message": "historical state unavailable",
    }


def test_log_validation_ignores_provider_metadata_but_audits_its_fields():
    bulk_log = _raw_log(include_block_timestamp=True)
    validation_log = _raw_log()
    checks, differences = cross_provider_consensus_checks(
        [bulk_log],
        _FixedLogClient([validation_log]),
        pool_address=str(bulk_log["address"]),
        topics=list(bulk_log["topics"]),
        sample_count=1,
        sample_width=1,
        minimum_block=100,
        maximum_block=100,
    )
    assert len(checks) == 1
    assert checks[0]["exact_match"] is True
    assert checks[0]["full_payload_exact_match"] is False
    assert checks[0]["provider_metadata_difference_only"] is True
    assert checks[0]["validation_status"] == (
        "consensus_match_provider_metadata_differs"
    )
    fields = {row["field"]: row for row in differences}
    assert fields["blockTimestamp"]["field_class"] == "provider_metadata"
    assert fields["blockTimestamp"]["primary_present_count"] == 1
    assert fields["blockTimestamp"]["validation_present_count"] == 0
    assert fields["blockTimestamp"]["exact_match"] is False
    assert all(fields[field]["exact_match"] for field in fields if field != "blockTimestamp")


def test_log_validation_only_canonicalizes_small_samples(monkeypatch):
    bulk_logs = [
        _raw_log(block_number=block, include_block_timestamp=True)
        for block in range(100, 200)
    ]

    class FilteredClient:
        def logs(self, parameters):
            start = int(parameters["fromBlock"], 16)
            end = int(parameters["toBlock"], 16)
            return [
                {key: value for key, value in log.items() if key != "blockTimestamp"}
                for log in bulk_logs
                if start <= int(str(log["blockNumber"]), 16) <= end
            ]

    original = real_v5.canonicalize_logs
    canonicalized_sizes = []

    def bounded_canonicalize(logs):
        canonicalized_sizes.append(len(logs))
        assert len(logs) <= 10
        return original(logs)

    monkeypatch.setattr(real_v5, "canonicalize_logs", bounded_canonicalize)
    checks, _ = cross_provider_consensus_checks(
        bulk_logs,
        FilteredClient(),
        pool_address=str(bulk_logs[0]["address"]),
        topics=list(bulk_logs[0]["topics"]),
        sample_count=4,
        sample_width=1,
        minimum_block=100,
        maximum_block=199,
    )
    assert len(checks) == 4
    assert all(row["exact_match"] for row in checks)
    assert max(canonicalized_sizes) == 1


def test_log_validation_keeps_gate_closed_for_consensus_field_mismatch():
    bulk_log = _raw_log(data="0x01", include_block_timestamp=True)
    validation_log = _raw_log(data="0x02")
    checks, differences = cross_provider_consensus_checks(
        [bulk_log],
        _FixedLogClient([validation_log]),
        pool_address=str(bulk_log["address"]),
        topics=list(bulk_log["topics"]),
        sample_count=1,
        sample_width=1,
        minimum_block=100,
        maximum_block=100,
    )
    assert checks[0]["exact_match"] is False
    assert checks[0]["validation_status"] == "consensus_mismatch"
    data_diagnostic = next(row for row in differences if row["field"] == "data")
    assert data_diagnostic["field_class"] == "consensus"
    assert data_diagnostic["value_mismatch_count"] == 1
    assert data_diagnostic["exact_match"] is False


def test_log_validation_rejects_missing_consensus_fields():
    bulk_log = _raw_log()
    validation_log = _raw_log()
    del validation_log["removed"]
    with pytest.raises(ValueError, match="missing consensus fields: removed"):
        cross_provider_consensus_checks(
            [bulk_log],
            _FixedLogClient([validation_log]),
            pool_address=str(bulk_log["address"]),
            topics=list(bulk_log["topics"]),
            sample_count=1,
            sample_width=1,
            minimum_block=100,
            maximum_block=100,
        )


def test_beneficiary_metrics_are_exact_and_keep_actor_upper_bound():
    result = beneficiary_metrics(["0xA", "0xa", "0xb", "0xc"])
    assert result["event_count"] == 4
    assert result["active_beneficiary_addresses"] == 3
    assert math.isclose(float(result["beneficiary_hhi"]), 0.375)
    assert math.isclose(float(result["top1_beneficiary_share"]), 0.5)
    assert math.isclose(float(result["inverse_hhi_beneficiary_addresses"]), 8 / 3)
    assert math.isclose(float(result["event_split_actor_hhi_lower"]), 0.25)
    assert result["actor_hhi_upper"] == 1.0


def test_weekly_panel_keeps_zero_event_weeks_and_no_causal_claim():
    records = pd.DataFrame(
        [
            {"event_week": -1, "action": "supply", "beneficiary_address": "0xa"},
            {"event_week": -1, "action": "borrow", "beneficiary_address": "0xb"},
        ]
    )
    rows = build_weekly_beneficiary_panel(
        records,
        _calendar("arbitrum_gho", 42161),
        chain="Arbitrum",
        chain_id=42161,
        cohort_id="arbitrum_gho",
    )
    assert len(rows) == 2
    assert rows[0]["event_count"] == 2
    assert rows[1]["event_count"] == 0
    assert {row["causal_status"] for row in rows} == {"descriptive_input_only"}


def test_comparable_panel_has_exactly_two_chains_per_event_week(tmp_path):
    ethereum = pd.DataFrame(
        [
            {"event_week": -1, "action": "supply", "beneficiary_address": "0xe1"},
            {"event_week": 0, "action": "borrow", "beneficiary_address": "0xe2"},
        ]
    )
    ethereum_path = tmp_path / "ethereum.csv"
    ethereum.to_csv(ethereum_path, index=False)
    arbitrum = pd.DataFrame(
        [
            {"event_week": -1, "action": "supply", "beneficiary_address": "0xa1"},
            {"event_week": 0, "action": "borrow", "beneficiary_address": "0xa2"},
        ]
    )
    rows = build_comparable_beneficiary_panel(
        ethereum_path,
        arbitrum,
        _calendar("ethereum_gho", 1),
        _calendar("arbitrum_gho", 42161),
    )
    assert len(rows) == 4
    assert {(row["event_week"], row["chain"]) for row in rows} == {
        (-1, "Ethereum"),
        (-1, "Arbitrum"),
        (0, "Ethereum"),
        (0, "Arbitrum"),
    }
