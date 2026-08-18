import json
from datetime import datetime, timezone

from aave_bns.real_v6_multichain import (
    build_support_audit,
    load_multichain_config,
    run_chain_preflight,
    validate_against_locked_sources,
    write_support_audit,
)

CONFIG_PATH = "configs/real_v6_multichain.yaml"


def test_multichain_registry_matches_locked_treatments_and_official_addresses():
    config = load_multichain_config(CONFIG_PATH)
    validate_against_locked_sources(config)
    chains = config["acquisition"]["chains"]
    assert chains["base"]["pool_address"] == "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
    assert chains["avalanche"]["pool_address"] == "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    assert chains["gnosis"]["pool_address"] == "0xb50201558B00496A145fE76f7424749556E326D8"
    assert chains["mantle"]["pool_address"] == "0x458F293454fE0d67EC0655f3672301301DD51422"


def test_support_audit_fails_closed_on_anticipation_and_bundled_market():
    config = load_multichain_config(CONFIG_PATH)
    audit = build_support_audit(config)
    summary = audit["summary"]
    assert summary["eligible_activation_did_cohorts"] == ["ethereum_gho", "arbitrum_gho"]
    rows = {row["cohort_id"]: row for row in audit["cohort_support"]}
    assert rows["ethereum_gho"]["clean_pre_week_count"] == 10
    assert rows["arbitrum_gho"]["clean_pre_week_count"] == 8
    assert rows["base_gho"]["clean_pre_week_count"] == 0
    assert rows["avalanche_gho"]["activation_did_design_gate"] == "false"
    assert rows["gnosis_gho"]["activation_did_design_gate"] == "false"
    assert "aave-market-and-gho-treatment-bundled" in rows["mantle_gho"]["failure_reasons"]
    assert summary["causal_estimate_produced"] is False


def test_common_calendar_donor_support_excludes_anticipated_chains():
    audit = build_support_audit(load_multichain_config(CONFIG_PATH))
    week_rows = {
        (row["target_cohort_id"], int(row["event_week"])): row
        for row in audit["cohort_week_support"]
    }
    assert "gnosis_gho" in week_rows[("arbitrum_gho", 16)]["eligible_donor_cohort_ids"]
    assert week_rows[("base_gho", 16)]["eligible_donor_count"] == 0
    assert week_rows[("mantle_gho", 0)]["eligible_donor_count"] == 0


def test_support_outputs_are_deterministic(tmp_path):
    config = load_multichain_config(CONFIG_PATH)
    first = write_support_audit(config, tmp_path / "first")
    second = write_support_audit(config, tmp_path / "second")
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes()


class _FakeRpcClient:
    def __init__(self, chain_id, activation_block, timestamp):
        self._chain_id = chain_id
        self._activation_block = activation_block
        self._timestamp = timestamp

    def chain_id(self):
        return self._chain_id

    def block(self, number):
        assert number == self._activation_block
        return {"hash": "0x" + "12" * 32, "timestamp": hex(self._timestamp)}

    def code(self, address, number):
        assert number == self._activation_block
        assert address.startswith("0x")
        return "0x60016000"

    def logs(self, parameters):
        assert parameters["fromBlock"] == hex(self._activation_block)
        return []


def test_public_preflight_passes_hard_checks_but_keeps_independence_gate_closed(tmp_path):
    config = load_multichain_config(CONFIG_PATH)
    cohort = next(row for row in config["cohorts"] if row["cohort_id"] == "base_gho")
    timestamp = int(
        datetime.fromisoformat(cohort["activation_utc"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )

    def factory(url, timeout_seconds, maximum_attempts):
        assert url == "https://mainnet.base.org"
        assert timeout_seconds > 0
        assert maximum_attempts > 0
        return _FakeRpcClient(8453, 26207512, timestamp)

    outputs = run_chain_preflight(
        config,
        "base",
        tmp_path,
        public_rpc_url="https://mainnet.base.org",
        client_factory=factory,
    )
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["hard_public_or_primary_validation_passed"] is True
    assert summary["primary_secret_configured"] is False
    assert summary["endpoints_independent"] is False
    assert summary["acquisition_ready"] is False
    assert summary["causal_estimate_produced"] is False


class _OrRejectingRpcClient(_FakeRpcClient):
    def logs(self, parameters):
        topics = parameters["topics"]
        if isinstance(topics[0], list):
            raise RuntimeError("provider rejects OR-topic filters")
        return super().logs(parameters)


def test_independent_single_topic_crosscheck_can_validate_public_bulk_source(tmp_path):
    config = load_multichain_config(CONFIG_PATH)
    config["acquisition"]["chains"]["gnosis"].pop("log_crosscheck_rpc_source_id")
    config["acquisition"]["chains"]["gnosis"].pop("log_crosscheck_rpc_url")
    cohort = next(row for row in config["cohorts"] if row["cohort_id"] == "gnosis_gho")
    timestamp = int(
        datetime.fromisoformat(cohort["activation_utc"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )

    def factory(url, timeout_seconds, maximum_attempts):
        assert timeout_seconds > 0
        assert maximum_attempts > 0
        if url == "https://primary.example/v2/secret":
            return _OrRejectingRpcClient(100, 41454924, timestamp)
        if url == "https://public.example":
            return _FakeRpcClient(100, 41454924, timestamp)
        raise AssertionError(f"unexpected endpoint: {url}")

    outputs = run_chain_preflight(
        config,
        "gnosis",
        tmp_path,
        rpc_url="https://primary.example/v2/secret",
        public_rpc_url="https://public.example",
        client_factory=factory,
    )
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["bulk_log_provider_role"] == "public"
    assert summary["bulk_log_query_mode"] == "or-topics"
    assert summary["crosscheck_log_provider_role"] == "primary"
    assert summary["crosscheck_log_query_mode"] == "single-topic-per-request"
    assert summary["primary_checks"][
        "maximum_supported_single_topic_log_probe_width"
    ] == 2000
    assert summary["independent_provider_match"] is True
    assert summary["acquisition_ready"] is True


def test_independent_endpoint_without_any_log_mode_stays_closed(tmp_path):
    config = load_multichain_config(CONFIG_PATH)
    config["acquisition"]["chains"]["gnosis"].pop("log_crosscheck_rpc_source_id")
    config["acquisition"]["chains"]["gnosis"].pop("log_crosscheck_rpc_url")
    cohort = next(row for row in config["cohorts"] if row["cohort_id"] == "gnosis_gho")
    timestamp = int(
        datetime.fromisoformat(cohort["activation_utc"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )

    class NoLogsClient(_FakeRpcClient):
        def logs(self, parameters):
            raise RuntimeError("eth_getLogs unavailable")

    def factory(url, timeout_seconds, maximum_attempts):
        del timeout_seconds, maximum_attempts
        if url == "https://primary.example/v2/secret":
            return NoLogsClient(100, 41454924, timestamp)
        return _FakeRpcClient(100, 41454924, timestamp)

    outputs = run_chain_preflight(
        config,
        "gnosis",
        tmp_path,
        rpc_url="https://primary.example/v2/secret",
        public_rpc_url="https://public.example",
        client_factory=factory,
    )
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["bulk_log_provider_role"] == "public"
    assert summary["crosscheck_log_provider_role"] == ""
    assert summary["acquisition_ready"] is False
    assert len(summary["primary_checks"]["diagnostics"]) == 6


def test_third_provider_can_crosscheck_public_bulk_when_primary_has_no_logs(tmp_path):
    config = load_multichain_config(CONFIG_PATH)
    cohort = next(row for row in config["cohorts"] if row["cohort_id"] == "gnosis_gho")
    timestamp = int(
        datetime.fromisoformat(cohort["activation_utc"].replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .timestamp()
    )

    class NoLogsClient(_FakeRpcClient):
        def logs(self, parameters):
            raise RuntimeError("eth_getLogs unavailable")

    def factory(url, timeout_seconds, maximum_attempts):
        del timeout_seconds, maximum_attempts
        if url == "https://primary.example/v2/secret":
            return NoLogsClient(100, 41454924, timestamp)
        if url in {"https://public.example", "https://rpc.gnosis.gateway.fm"}:
            return _FakeRpcClient(100, 41454924, timestamp)
        raise AssertionError(f"unexpected endpoint: {url}")

    outputs = run_chain_preflight(
        config,
        "gnosis",
        tmp_path,
        rpc_url="https://primary.example/v2/secret",
        public_rpc_url="https://public.example",
        client_factory=factory,
    )
    summary = json.loads(outputs["summary"].read_text(encoding="utf-8"))
    assert summary["bulk_log_provider_role"] == "public"
    assert summary["crosscheck_log_provider_role"] == "log_crosscheck"
    assert summary["crosscheck_log_query_mode"] == "or-topics"
    assert summary["log_crosscheck_endpoints_independent"] is True
    assert summary["log_crosscheck_provider_match"] is True
    assert summary["acquisition_ready"] is True
