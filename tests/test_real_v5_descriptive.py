import json

import pandas as pd

from aave_bns.real_v5_descriptive import (
    cross_chain_overlap,
    run_descriptive_analysis,
    weekly_address_metrics,
)


def _events(chain="Ethereum", chain_id=1):
    return pd.DataFrame(
        [
            {"event_week": -16, "action": "supply", "beneficiary_address": "0xa"},
            {"event_week": -16, "action": "borrow", "beneficiary_address": "0xa"},
            {"event_week": -16, "action": "supply", "beneficiary_address": "0xb"},
            {"event_week": -15, "action": "supply", "beneficiary_address": "0xb"},
            {"event_week": -15, "action": "repay", "beneficiary_address": "0xc"},
        ]
    ).assign(chain=chain, chain_id=chain_id)


def test_weekly_metrics_are_exact_and_zero_filled():
    result = weekly_address_metrics(_events())
    first = result.iloc[0]
    assert len(result) == 33
    assert first.event_count == 3
    assert first.active_beneficiary_addresses == 2
    assert first.entrant_addresses == 2
    assert first.beneficiary_hhi == 5 / 9
    assert first.nakamoto_51 == 1
    assert result.iloc[-1].event_count == 0
    assert set(result.causal_status) == {"descriptive_only"}


def test_overlap_uses_relative_event_week_and_discloses_clock():
    ethereum = _events()
    arbitrum = _events("Arbitrum", 42161)
    arbitrum.loc[arbitrum.beneficiary_address == "0xa", "beneficiary_address"] = "0xd"
    row = cross_chain_overlap(ethereum, arbitrum).iloc[0]
    assert row.shared_addresses == 1
    assert row.jaccard_overlap == 1 / 3
    assert "chain-relative" in row.timing_note


def test_pipeline_keeps_entity_causal_and_topology_gates_closed(tmp_path):
    eth = tmp_path / "eth.csv"
    arb = tmp_path / "arb.csv"
    _events().drop(columns=["chain", "chain_id"]).to_csv(eth, index=False)
    _events("Arbitrum", 42161).drop(columns=["chain", "chain_id"]).to_csv(arb, index=False)
    paths = run_descriptive_analysis(eth, arb, tmp_path / "out")
    summary = json.loads(paths["summary"].read_text())
    assert summary["weekly_metric_rows"] == 66
    assert summary["causal_estimate_produced"] is False
    assert summary["entity_level_primary_result_produced"] is False
    assert summary["structural_network_result_produced"] is False
    assert summary["infrastructure_dependence_result_produced"] is False
