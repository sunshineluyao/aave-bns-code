from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_causal_v2_mvp_support", ROOT / "scripts/build_causal_v2_mvp_support.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
build_support = MODULE.build_support


def _utc(days: int) -> str:
    return (datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def test_mvp_gate_uses_verified_contemporaneous_donors_and_excludes_bundle():
    treatments = []
    for index in range(4):
        treatments.append({
            "cohort_id": f"c{index}", "chain": f"C{index}", "chain_id": str(index + 1),
            "formal_commitment_utc": _utc(300 + index),
            "operational_activation_utc": _utc(330 + index),
            "bundled_market_entry": "true" if index == 3 else "false",
        })
    donors = [
        {"market_id": "d1", "chain_id": "100", "status": "verified", "pool_first_code_timestamp_utc": _utc(1)},
        {"market_id": "d2", "chain_id": "101", "status": "verified", "pool_first_code_timestamp_utc": _utc(2)},
        {"market_id": "d3", "chain_id": "102", "status": "failed", "pool_first_code_timestamp_utc": _utc(3)},
    ]
    rows, summary = build_support(treatments, donors)
    assert summary["formal_commitment_supported_cohorts"] == ["c0", "c1", "c2"]
    assert summary["formal_commitment_mvp_gate"] is True
    bundled = next(row for row in rows if row["cohort_id"] == "c3" and row["clock"] == "formal_commitment")
    assert bundled["eligible_donor_count"] == 2
    assert bundled["support_gate"] == "false"


def test_donor_must_cover_full_pre_window():
    treatment = [{
        "cohort_id": "c", "chain": "C", "chain_id": "1",
        "formal_commitment_utc": _utc(200), "operational_activation_utc": _utc(220),
        "bundled_market_entry": "false",
    }]
    donors = [
        {"market_id": "early", "chain_id": "2", "status": "verified", "pool_first_code_timestamp_utc": _utc(1)},
        {"market_id": "late", "chain_id": "3", "status": "verified", "pool_first_code_timestamp_utc": _utc(100)},
    ]
    rows, _ = build_support(treatment, donors, window_weeks=16)
    formal = next(row for row in rows if row["clock"] == "formal_commitment")
    assert formal["eligible_market_ids"] == "early"
    assert formal["support_gate"] == "false"
