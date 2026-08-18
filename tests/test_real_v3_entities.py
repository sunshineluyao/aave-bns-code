from __future__ import annotations

import pandas as pd

from aave_bns.real_v3_entities import (
    build_address_universe,
    build_measurement_panels,
    build_versioned_registry,
    classify_code_snapshots,
    deterministic_validation_sample,
    fetch_address_code_snapshots,
)

A = "0x" + "1" * 40
B = "0x" + "2" * 40
C = "0x" + "3" * 40


def fixture_events() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "chain_id": 1,
                "action": "supply",
                "block_number": 100,
                "tx_hash": "0xaaa",
                "log_index": 1,
                "event_week": -1,
                "actor_address": A,
                "beneficiary_address": A,
                "counterparty_address": None,
            },
            {
                "chain_id": 1,
                "action": "withdraw",
                "block_number": 110,
                "tx_hash": "0xbbb",
                "log_index": 2,
                "event_week": 0,
                "actor_address": B,
                "beneficiary_address": C,
                "counterparty_address": C,
            },
        ]
    )


def test_address_universe_deduplicates_same_address_roles_within_event():
    universe, incidences = build_address_universe(fixture_events())
    assert len(universe) == 3
    assert len(incidences) == 3
    row_a = universe.set_index("address").loc[A]
    assert row_a["observed_roles"] == "actor|beneficiary"
    assert row_a["event_incidence_count"] == 1
    row_c = universe.set_index("address").loc[C]
    assert row_c["observed_roles"] == "beneficiary|counterparty"
    assert row_c["event_incidence_count"] == 1


def test_code_classification_keeps_templates_separate_from_entities():
    code = "0x6001600055"
    records = [
        {
            "address": A,
            "first_observed_block": 100,
            "first_runtime_code": "0x",
            "last_observed_block": 100,
            "last_runtime_code": "0x",
        },
        {
            "address": B,
            "first_observed_block": 110,
            "first_runtime_code": code,
            "last_observed_block": 120,
            "last_runtime_code": code,
        },
        {
            "address": C,
            "first_observed_block": 110,
            "first_runtime_code": "0x",
            "last_observed_block": 120,
            "last_runtime_code": code,
        },
    ]
    facts = classify_code_snapshots(records).set_index("address")
    assert facts.loc[A, "address_type"] == "code_absent_at_observed_bounds"
    assert facts.loc[B, "address_type"] == "smart_contract"
    assert facts.loc[B, "infrastructure_family_id"].startswith("runtime_sha256:")
    assert facts.loc[C, "address_type"] == "smart_contract_dynamic"
    assert facts.loc[C, "infrastructure_family_id"] == f"dynamic_address:{C}"


def test_curated_entity_collapse_is_sensitivity_not_unknown_clustering():
    universe, incidences = build_address_universe(fixture_events())
    code = "0x6001600055"
    code_facts = classify_code_snapshots(
        [
            {
                "address": address,
                "first_observed_block": int(row.first_observed_block),
                "first_runtime_code": code,
                "last_observed_block": int(row.last_observed_block),
                "last_runtime_code": code,
            }
            for row in universe.itertuples(index=False)
            for address in [row.address]
        ]
    )
    labels = pd.DataFrame(
        [
            {
                "release_version": "v-test",
                "address": B,
                "address_label": "Adapter one",
                "entity_id": "protocol:test",
                "entity_label": "Test protocol",
                "entity_category": "adapter",
                "entity_scope": "protocol_infrastructure",
                "infrastructure_category": "adapter",
                "source_id": "official",
                "source_url": "https://example.test/source",
                "source_revision": "a" * 40,
                "source_path": "registry.sol",
                "valid_from_block": 100,
                "valid_to_block": 120,
                "confidence": 1.0,
                "review_status": "primary_source_verified",
                "notes": "test",
            },
            {
                "release_version": "v-test",
                "address": C,
                "address_label": "Adapter two",
                "entity_id": "protocol:test",
                "entity_label": "Test protocol",
                "entity_category": "adapter",
                "entity_scope": "protocol_infrastructure",
                "infrastructure_category": "adapter",
                "source_id": "official",
                "source_url": "https://example.test/source",
                "source_revision": "a" * 40,
                "source_path": "registry.sol",
                "valid_from_block": 100,
                "valid_to_block": 120,
                "confidence": 1.0,
                "review_status": "primary_source_verified",
                "notes": "test",
            },
        ]
    )
    registry = build_versioned_registry(
        universe,
        code_facts,
        labels,
        release_version="v-test",
        minimum_confidence=0.9,
    )
    weekly_action, weekly = build_measurement_panels(incidences, registry)
    event_zero = weekly.set_index("event_week").loc[0]
    assert event_zero["active_addresses"] == 2
    assert event_zero["effective_active_addresses"] == 2
    assert event_zero["effective_curated_entities_sensitivity"] == 1
    assert event_zero["economic_actor_incidence_coverage"] == 0
    assert len(weekly_action) == 4
    assert weekly_action["event_incidence_count"].eq(0).sum() == 2


def test_validation_sample_is_deterministic_for_string_hash_ranks():
    registry = pd.DataFrame(
        {
            "address": ["0x" + f"{value:040x}" for value in range(1, 21)],
            "contract_observed": [value % 2 == 0 for value in range(1, 21)],
            "event_incidence_count": list(range(1, 21)),
        }
    )
    first = deterministic_validation_sample(registry, 8)
    second = deterministic_validation_sample(registry.sample(frac=1, random_state=7), 8)
    assert first["address"].tolist() == second["address"].tolist()
    assert len(first) == 8


def test_code_batch_metadata_is_stable_after_resume(tmp_path):
    universe = pd.DataFrame(
        [
            {
                "address": A,
                "first_observed_block": 100,
                "last_observed_block": 110,
            }
        ]
    )

    class FreshClient:
        def batch_call(self, calls):
            assert len(calls) == 2
            return ["0x", "0x"]

    class ResumeClient:
        def batch_call(self, calls):  # pragma: no cover - must not query RPC
            raise AssertionError(f"resume unexpectedly queried {calls}")

    raw_directory = tmp_path / "data/raw/real_v3/ethereum/address_code_batches"
    fresh_records, fresh_metadata = fetch_address_code_snapshots(
        universe,
        client=FreshClient(),
        raw_directory=raw_directory,
        project_root=tmp_path,
        addresses_per_batch=50,
        workers=1,
        resume=False,
        progress_every=0,
    )
    resumed_records, resumed_metadata = fetch_address_code_snapshots(
        universe,
        client=ResumeClient(),
        raw_directory=raw_directory,
        project_root=tmp_path,
        addresses_per_batch=50,
        workers=1,
        resume=True,
        progress_every=0,
    )

    assert fresh_records == resumed_records
    assert fresh_metadata == resumed_metadata
    assert fresh_metadata[0]["source"] == "primary_rpc"
