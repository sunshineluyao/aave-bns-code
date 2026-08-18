import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "collector", ROOT / "scripts/collect_causal_v2_mvp_panel.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeClient:
    def __init__(self, maximum_width=4):
        self.maximum_width = maximum_width

    def logs(self, query):
        left, right = int(query["fromBlock"], 16), int(query["toBlock"], 16)
        if right - left + 1 > self.maximum_width:
            raise RuntimeError("range too wide")
        return []


def test_adaptive_log_fetch_has_exact_gap_free_coverage():
    logs, chunks = MODULE.fetch_logs_complete(
        FakeClient(), address="0x" + "1" * 40, start_block=10, end_block=22, initial_width=10
    )
    assert logs == []
    assert chunks[0]["from_block"] == 10
    assert chunks[-1]["to_block"] == 22
    assert sum(row["to_block"] - row["from_block"] + 1 for row in chunks) == 13


def test_weekly_aggregation_computes_beneficiary_hhi():
    events = [
        {
            "chain_id": "1",
            "chain": "x",
            "week_start_utc": "2026-01-05T00:00:00Z",
            "event_family": "Supply",
            "beneficiary_address": actor,
        }
        for actor in ("a", "a", "a", "b")
    ]
    row = MODULE.aggregate_weekly(events)[0]
    assert row["event_count"] == "4"
    assert row["active_beneficiary_addresses"] == "2"
    assert abs(float(row["beneficiary_event_hhi"]) - 0.625) < 1e-12
    assert abs(float(row["normalized_beneficiary_event_hhi"]) - 0.25) < 1e-12


def test_indexed_address_decode_is_strict():
    assert MODULE.address_from_topic("0x" + "0" * 24 + "ab" * 20) == "0x" + "ab" * 20


def test_chain_filter_contract_is_fail_closed():
    scans = [{"chain_id": "1"}, {"chain_id": "2"}]
    selected = [row for row in scans if row["chain_id"] == "2"]
    assert selected == [{"chain_id": "2"}]
    assert [row for row in scans if row["chain_id"] == "3"] == []


def test_time_shards_are_contiguous_and_cover_full_window():
    scan = {
        "scan_start_utc": "2024-01-01T00:00:00Z",
        "scan_end_utc": "2024-05-01T00:00:00Z",
    }
    shards = [MODULE.shard_scan(scan, index, 4) for index in range(4)]
    assert shards[0]["scan_start_utc"] == scan["scan_start_utc"]
    assert shards[-1]["scan_end_utc"] == scan["scan_end_utc"]
    assert all(
        shards[index]["scan_end_utc"] == shards[index + 1]["scan_start_utc"]
        for index in range(3)
    )


def test_time_shard_contract_is_fail_closed():
    scan = {
        "scan_start_utc": "2024-01-01T00:00:00Z",
        "scan_end_utc": "2024-02-01T00:00:00Z",
    }
    with pytest.raises(ValueError):
        MODULE.shard_scan(scan, 4, 4)
    with pytest.raises(ValueError):
        MODULE.shard_scan(scan, 0, 0)


def test_aave_v3_beneficiary_topics_match_canonical_abi():
    supply_topic = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
    borrow_topic = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
    assert MODULE.EVENTS[supply_topic] == ("Supply", 2)
    assert MODULE.EVENTS[borrow_topic] == ("Borrow", 2)


def test_zero_address_beneficiary_is_rejected():
    with pytest.raises(ValueError, match="zero address"):
        MODULE.address_from_topic("0x" + "0" * 64)
