import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_causal_v2_acquisition_manifest",
    ROOT / "scripts/build_causal_v2_acquisition_manifest.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_manifest_keeps_supported_cohorts_and_deduplicates_scans():
    support = []
    for index, cohort in enumerate(("base_gho", "avalanche_gho", "gnosis_gho")):
        for clock in ("formal_commitment", "operational_activation"):
            support.append({
                "cohort_id": cohort,
                "clock": clock,
                "event_utc": f"2025-0{index + 2}-01T00:00:00Z",
                "window_weeks": "16",
                "eligible_market_ids": "metis_v3;scroll_v3",
                "support_gate": "true",
            })
    support.append({
        "cohort_id": "mantle_gho", "clock": "formal_commitment",
        "event_utc": "2025-01-01T00:00:00Z", "window_weeks": "16",
        "eligible_market_ids": "metis_v3;scroll_v3", "support_gate": "false",
    })
    treatments = [
        {"cohort_id": cohort, "chain_id": str(chain_id)}
        for cohort, chain_id in (("base_gho", 8453), ("avalanche_gho", 43114),
                                 ("gnosis_gho", 100), ("mantle_gho", 5000))
    ]
    treated = [
        {"chain": cohort, "chain_id": str(chain_id), "pool_address": f"0x{chain_id}",
         "rpc_url_env": f"RPC_{chain_id}", "verification_status": "verified_locked_registry"}
        for cohort, chain_id in (("Base", 8453), ("Avalanche", 43114), ("Gnosis", 100))
    ]
    donors = [
        {"market_id": market_id, "chain": market_id, "chain_id": str(chain_id),
         "pool_address": f"0xd{chain_id}", "rpc_url_env": f"RPC_D{chain_id}"}
        for market_id, chain_id in (("metis_v3", 1088), ("scroll_v3", 534352))
    ]
    jobs, scans, summary = MODULE.build_manifest(support, treatments, treated, donors)
    assert summary["analysis_count"] == 6
    assert summary["minimum_donors_per_analysis"] == 2
    assert "mantle_gho" not in {row["cohort_id"] for row in jobs}
    assert len(scans) == 5
    assert all(row["event_families"] == "Supply;Withdraw;Borrow;Repay;LiquidationCall" for row in scans)


def test_manifest_fails_closed_when_treated_pool_is_unverified():
    support = [{
        "cohort_id": "base_gho", "clock": "formal_commitment",
        "event_utc": "2025-01-01T00:00:00Z", "window_weeks": "16",
        "eligible_market_ids": "d1;d2", "support_gate": "true",
    }]
    treatments = [{"cohort_id": "base_gho", "chain_id": "8453"}]
    treated = [{"chain": "Base", "chain_id": "8453", "pool_address": "0x1",
                "rpc_url_env": "BASE_RPC_URL", "verification_status": "pending"}]
    donors = [
        {"market_id": key, "chain": key, "chain_id": str(index),
         "pool_address": f"0x{index}", "rpc_url_env": f"RPC_{index}"}
        for index, key in enumerate(("d1", "d2"), start=10)
    ]
    try:
        MODULE.build_manifest(support, treatments, treated, donors)
    except ValueError as exc:
        assert "missing verified treated Pool" in str(exc)
    else:
        raise AssertionError("unverified treated Pool must fail closed")

