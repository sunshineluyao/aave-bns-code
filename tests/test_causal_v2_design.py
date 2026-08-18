from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_causal_v2_design", ROOT / "scripts/audit_causal_v2_design.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_registry_is_internally_valid_and_fail_closed():
    result = MODULE.audit()
    assert result["errors"] == []
    assert result["treatment_cohort_count"] == 6
    assert result["formal_commitment_staggered_did_gate"] is False
    assert result["bridge_metric_gate"] is False
    assert result["causal_estimate_produced"] is False
    assert result["infrastructure_result_produced"] is False


def test_mantle_is_explicitly_bundled():
    rows = MODULE.read_csv(ROOT / "data/metadata/causal_v2_treatment_registry.csv")
    mantle = next(row for row in rows if row["cohort_id"] == "mantle_gho")
    assert mantle["bundled_market_entry"] == "true"
    assert mantle["market_available_by_utc"] == mantle["operational_activation_utc"]

