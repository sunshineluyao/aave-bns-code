from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath

OUTPUT = Path("outputs/real_v3/ethereum")
LOCK = Path("data/metadata/real_v3_ethereum_release_lock.json")


def read_csv(path: Path, *, compressed: bool = False) -> list[dict[str, str]]:
    if compressed:
        handle = gzip.open(path, "rt", encoding="utf-8", newline="")
    else:
        handle = path.open(newline="", encoding="utf-8")
    with handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_real_v3_release_is_locked_and_fails_closed():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))

    assert summary["address_count"] == 15762
    assert summary["smart_contract_address_count"] == 1128
    assert summary["event_incidence_count"] == 148437
    assert summary["entity_gate"]["passed"] is False
    assert summary["economic_actor_event_incidence_coverage"] == 0
    assert summary["entity_level_primary_result_produced"] is False
    assert summary["causal_estimate_produced"] is False
    assert manifest["entity_gate_passed"] is False
    assert manifest["entity_level_primary_result_produced"] is False
    assert manifest["causal_estimate_produced"] is False
    assert manifest["artifacts"] == lock["published_artifacts"]
    for relative, expected in lock["published_artifacts"].items():
        assert sha256_file(Path(relative)) == expected

    for name in ("curated_labels", "source_catalog"):
        item = lock["inputs"][name]
        assert sha256_file(Path(item["path"])) == item["sha256"]
        assert manifest["inputs"][item["path"]] == item["sha256"]


def test_real_v3_registry_never_equates_code_absence_with_a_person():
    rows = read_csv(OUTPUT / "address_registry.csv.gz", compressed=True)
    assert len(rows) == 15762
    assert len({row["address"] for row in rows}) == len(rows)
    assert all(row["release_version"] == "real_v3-ethereum-v0.1.0" for row in rows)

    unresolved = [row for row in rows if row["high_confidence_entity_label"] == "False"]
    assert all(
        row["curated_entity_key"] == f"address:{row['address']}" for row in unresolved
    )
    code_absent = [
        row for row in rows if row["address_type"] == "code_absent_at_observed_bounds"
    ]
    assert len(code_absent) == 14634
    assert all(row["contract_observed"] == "False" for row in code_absent)
    assert not any(row["economic_actor_resolved"] == "True" for row in rows)


def test_real_v3_primary_labels_are_pinned_and_catalogued():
    labels = read_csv(Path("data/metadata/real_v3_ethereum_curated_labels.csv"))
    catalog = read_csv(Path("data/metadata/source_catalog.csv"))
    source_ids = {row["source_id"] for row in catalog}

    assert len(labels) == 10
    assert len({row["address"] for row in labels}) == 10
    assert all(row["source_id"] == "aave_address_book" for row in labels)
    assert all(row["source_id"] in source_ids for row in labels)
    assert all(f"/blob/{row['source_revision']}/" in row["source_url"] for row in labels)
    assert all(row["review_status"] == "primary_source_verified" for row in labels)
    assert all(
        row["entity_scope"] in {"protocol_infrastructure", "asset_infrastructure"}
        for row in labels
    )


def test_real_v3_paths_and_resume_metadata_are_portable():
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    batches = read_csv(OUTPUT / "code_retrieval_batches.csv")

    assert manifest["path_policy"] == "repository_relative_posix"
    assert len(batches) == len(manifest["raw_batches"]) == 316
    assert {row["source"] for row in batches} == {"primary_rpc"}
    for row in [*batches, *manifest["raw_batches"]]:
        path = PurePosixPath(row["path"])
        assert not path.is_absolute()
        assert ".." not in path.parts


def test_real_v3_panels_are_complete_sensitivities_not_estimates():
    weekly = read_csv(OUTPUT / "weekly_entity_sensitivity.csv")
    weekly_action = read_csv(OUTPUT / "weekly_action_entity_sensitivity.csv")
    actions = {"borrow", "liquidation", "repay", "supply", "withdraw"}

    assert {int(row["event_week"]) for row in weekly} == set(range(-16, 17))
    assert {
        (int(row["event_week"]), row["action"]) for row in weekly_action
    } == {(week, action) for week in range(-16, 17) for action in actions}
    assert sum(int(row["event_incidence_count"]) for row in weekly) == 148437
    assert sum(int(row["event_incidence_count"]) for row in weekly_action) == 148437
    assert all(float(row["economic_actor_incidence_coverage"]) == 0 for row in weekly)

    markdown = Path("docs/REAL_V3_ENTITY_LAYER.md").read_text(encoding="utf-8")
    latex = Path("paper/appendix/real_v3_entity_audit.tex").read_text(encoding="utf-8")
    assert "no primary entity-level result and no causal estimate" in markdown
    assert "Failed closed" in latex
    assert "Causal estimate produced & No" in latex
    assert "not treatment effects" in markdown
