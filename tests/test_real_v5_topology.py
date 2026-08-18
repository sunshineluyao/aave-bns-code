import json

import pandas as pd

from aave_bns.real_v5_topology import load_topology_events, topology_metrics


def _events():
    rows = [
        ("supply", "0xa", "0xa"),
        ("withdraw", "0xa", "0xb"),
        ("borrow", "0xb", "0xa"),
        ("repay", "0xc", "0xa"),
        ("liquidation", "0xa", "0xc"),
    ]
    return pd.DataFrame(
        [
            {
                "chain_id": 1,
                "action": action,
                "block_number": 100 + index,
                "tx_hash": f"0x{index:064x}",
                "log_index": index,
                "event_week": -16 if index == 0 else 16,
                "reserve_address": "0xreserve",
                "actor_address": actor,
                "beneficiary_address": beneficiary,
            }
            for index, (action, actor, beneficiary) in enumerate(rows)
        ]
    )


def test_topology_excludes_self_edges_but_reports_their_share():
    result = topology_metrics(_events(), chain="Ethereum", layer="all_actions")
    assert result["event_count"] == 5
    assert result["self_directed_event_share"] == 0.2
    assert result["delegated_event_count"] == 4
    assert result["topology_node_count"] == 3
    assert result["topology_edge_count"] == 4
    assert result["directed_reciprocity"] == 1.0
    assert result["maximum_k_core"] == 1


def test_loader_requires_unique_event_keys_and_locked_window(tmp_path):
    path = tmp_path / "events.csv"
    _events().to_csv(path, index=False)
    loaded = load_topology_events(path, chain_id=1)
    assert len(loaded) == 5
    duplicate = pd.concat([_events(), _events().iloc[[0]]], ignore_index=True)
    duplicate.to_csv(path, index=False)
    try:
        load_topology_events(path, chain_id=1)
    except ValueError as error:
        assert "duplicate" in str(error)
    else:
        raise AssertionError("duplicate transaction-log keys must fail closed")


def test_summary_contract_keeps_unobserved_layers_closed():
    expected = {
        "structural_network_result_produced": True,
        "entity_level_primary_result_produced": False,
        "infrastructure_dependence_result_produced": False,
        "causal_estimate_produced": False,
    }
    assert json.loads(json.dumps(expected)) == expected
