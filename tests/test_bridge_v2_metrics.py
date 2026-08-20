import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bridge", ROOT / "scripts/compute_bridge_v2_metrics.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(message, route, source, amount):
    return {
        "week": "2026-W01", "message_id": message, "source_chain": source,
        "destination_chain": "Gnosis", "route_id": route, "amount_wei": str(amount),
        "local_activity_wei": "100", "source_tx_hash": "0x1",
        "destination_tx_hash": "0x2", "verification_status": "onchain_verified_paired",
    }


def test_two_route_metrics_are_exactly_interpretable():
    metrics, summary = MODULE.compute([
        row("0xa", "eth-gnosis", "Ethereum", 60),
        row("0xb", "arb-gnosis", "Arbitrum", 40),
    ])
    assert summary["gate_passed"] is True
    assert metrics[0]["route_hhi"] == 0.52
    assert metrics[0]["normalized_route_hhi"] == 0.040000000000000036
    assert metrics[0]["bridge_reliance"] == 0.5
    assert metrics[0]["largest_route_removal_loss"] == 0.6


def test_single_route_fails_closed():
    _, summary = MODULE.compute([row("0xa", "eth-gnosis", "Ethereum", 60)])
    assert summary["gate_passed"] is False
    assert summary["infrastructure_result_produced"] is False


