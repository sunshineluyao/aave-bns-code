from __future__ import annotations

import pandas as pd
import pytest

from aave_bns.real_v4_partial_identification import (
    REQUIRED_CONSTRAINT_COLUMNS,
    build_beneficiary_event_panel,
    build_bound_panels,
    build_change_bounds,
    load_actor_constraints,
    validate_event_timing,
)

A = "0x" + "1" * 40
B = "0x" + "2" * 40
C = "0x" + "3" * 40


def fixture_events() -> pd.DataFrame:
    actions = ["borrow", "liquidation", "repay", "supply", "withdraw"]
    addresses = [A, A, B, B, C, A, B, C, C, C]
    rows = []
    for index, (action, address) in enumerate(
        zip(actions * 2, addresses, strict=True), start=1
    ):
        rows.append(
            {
                "chain_id": 1,
                "action": action,
                "block_number": 100 + index,
                "tx_hash": "0x" + f"{index:064x}",
                "log_index": index,
                "event_week": -1 if index <= 5 else 1,
                "beneficiary_address": address,
            }
        )
    return pd.DataFrame(rows)


def fixture_registry() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "address": [A, B, C],
            "contract_observed": [False, True, False],
            "curated_infrastructure": [False, False, False],
            "economic_actor_resolved": [False, False, False],
        }
    )


def empty_constraints() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "constraint_id",
            "left_address",
            "right_address",
            "valid_from_block",
            "valid_to_block",
        ]
    )


def test_timing_validation_selects_the_requested_event_instead_of_a_hard_coded_cohort(
    tmp_path,
):
    audit = tmp_path / "audit.csv"
    calendar = tmp_path / "calendar.csv"
    pd.DataFrame(
        [
            {
                "event_id": "arbitrum_gho_policy",
                "event_time_utc": "2024-07-02T15:40:32Z",
                "block_number": 228027379,
            }
        ]
    ).to_csv(audit, index=False)
    pd.DataFrame(
        [
            {
                "event_id": "arbitrum_gho_policy",
                "cohort_id": "arbitrum_gho",
                "event_week": 0,
                "activation_block": 228027379,
                "activation_utc": "2024-07-02T15:40:32Z",
            }
        ]
    ).to_csv(calendar, index=False)
    validate_event_timing(
        audit,
        calendar,
        event_id="arbitrum_gho_policy",
        activation_block=228027379,
        activation_utc="2024-07-02T15:40:32Z",
    )


def test_beneficiary_panel_is_one_position_holder_per_event():
    panel = build_beneficiary_event_panel(
        fixture_events(), chain_id=1, minimum_event_week=-1, maximum_event_week=1
    )
    assert len(panel) == 10
    assert panel["event_ordinal"].tolist() == list(range(1, 11))
    assert set(panel["beneficiary_address"]) == {A, B, C}
    assert panel["event_week"].tolist() == [-1] * 5 + [1] * 5


def test_logical_and_stable_address_hhi_bounds_are_distinct():
    panel = build_beneficiary_event_panel(
        fixture_events(), chain_id=1, minimum_event_week=-1, maximum_event_week=1
    )
    periods = [
        {"name": "pre", "minimum_event_week": -1, "maximum_event_week": -1},
        {"name": "post", "minimum_event_week": 1, "maximum_event_week": 1},
        {"name": "full", "minimum_event_week": -1, "maximum_event_week": 1},
    ]
    weekly_action, weekly, period = build_bound_panels(
        panel, fixture_registry(), empty_constraints(), periods
    )
    full = period.set_index("period").loc["full"]
    # Full weights are A=3, B=3, C=4.
    assert full["event_split_hhi_lower"] == pytest.approx(0.1)
    assert full["stable_address_hhi_lower"] == pytest.approx(0.34)
    assert full["stable_address_hhi_upper"] == 1.0
    assert full["stable_address_effective_upper"] == pytest.approx(1 / 0.34)
    assert len(weekly) == 2
    assert len(weekly_action) == 15
    assert int((weekly_action["event_count"] == 0).sum()) == 5


def test_primary_source_must_link_tightens_only_the_evidence_lower_bound():
    panel = build_beneficiary_event_panel(
        fixture_events(), chain_id=1, minimum_event_week=-1, maximum_event_week=1
    )
    constraints = pd.DataFrame(
        [
            {
                "constraint_id": "b-c",
                "left_address": B,
                "right_address": C,
                "valid_from_block": 0,
                "valid_to_block": 999,
            }
        ]
    )
    periods = [
        {"name": "pre", "minimum_event_week": -1, "maximum_event_week": -1},
        {"name": "post", "minimum_event_week": 1, "maximum_event_week": 1},
        {"name": "full", "minimum_event_week": -1, "maximum_event_week": 1},
    ]
    _, _, period = build_bound_panels(panel, fixture_registry(), constraints, periods)
    full = period.set_index("period").loc["full"]
    # Must-link B and C gives component weights 3 and 7.
    assert full["stable_address_hhi_lower"] == pytest.approx(0.34)
    assert full["evidence_hhi_lower"] == pytest.approx(0.58)
    assert full["accepted_must_link_constraint_count"] == 1


def test_change_bounds_do_not_promote_address_direction_to_actor_direction():
    panel = build_beneficiary_event_panel(
        fixture_events(), chain_id=1, minimum_event_week=-1, maximum_event_week=1
    )
    periods = [
        {"name": "pre", "minimum_event_week": -1, "maximum_event_week": -1},
        {"name": "post", "minimum_event_week": 1, "maximum_event_week": 1},
    ]
    _, _, period = build_bound_panels(
        panel, fixture_registry(), empty_constraints(), periods
    )
    changes = build_change_bounds(period).set_index("assumption")
    assert bool(changes.loc["address_proxy_point", "sign_identified"])
    assert not bool(
        changes.loc["address_proxy_point", "economic_actor_conclusion_permitted"]
    )
    assert changes.loc["stable_address", "change_lower"] < 0
    assert changes.loc["stable_address", "change_upper"] > 0
    assert changes.loc["stable_address", "direction"] == "not_identified"


def test_constraint_loader_rejects_non_primary_heuristic_links(tmp_path):
    row = {column: "" for column in REQUIRED_CONSTRAINT_COLUMNS}
    row.update(
        {
            "constraint_id": "heuristic",
            "release_version": "test-v1",
            "chain_id": "1",
            "left_address": A,
            "right_address": B,
            "relation": "must_link",
            "entity_scope": "economic_actor",
            "source_id": "behavior_similarity",
            "source_url": "https://example.test/evidence",
            "source_revision": "a" * 40,
            "source_path": "evidence.csv",
            "valid_from_block": "1",
            "valid_to_block": "999",
            "confidence": "0.95",
            "review_status": "heuristic",
        }
    )
    path = tmp_path / "constraints.csv"
    pd.DataFrame([row]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="must pass the evidence gate"):
        load_actor_constraints(
            path,
            release_version="test-v1",
            chain_id=1,
            minimum_confidence=0.9,
            allowed_relation="must_link",
            required_entity_scope="economic_actor",
            required_review_status="primary_source_verified",
            registry_addresses={A, B},
        )
