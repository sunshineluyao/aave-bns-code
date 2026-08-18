from aave_bns.real_v2 import (
    build_event_week_calendar,
    load_real_v2_config,
    treatment_cohorts,
    validate_against_event_ledger,
)


def test_treatment_clock_matches_audited_event_ledger():
    config = load_real_v2_config("configs/real_v2.yaml")
    validate_against_event_ledger(config, "data/metadata/event_source_audit.csv")
    cohorts = treatment_cohorts(config)
    assert len(cohorts) == 6
    assert {row.event_id for row in cohorts} == {
        "ethereum_gho_policy",
        "arbitrum_gho_policy",
        "base_gho_policy",
        "avalanche_gho_policy",
        "gnosis_gho_policy",
        "mantle_gho_policy",
    }


def test_event_week_calendar_is_symmetric_and_complete():
    config = load_real_v2_config("configs/real_v2.yaml")
    rows = build_event_week_calendar(config)
    assert len(rows) == 6 * 33
    for cohort_id in {row["cohort_id"] for row in rows}:
        cohort_rows = [row for row in rows if row["cohort_id"] == cohort_id]
        assert [row["event_week"] for row in cohort_rows] == list(range(-16, 17))
        assert sum(row["event_week"] == 0 for row in cohort_rows) == 1
