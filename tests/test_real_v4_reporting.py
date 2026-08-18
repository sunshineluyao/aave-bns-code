from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _sha256(relative: str) -> str:
    digest = hashlib.sha256()
    with (ROOT / relative).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_release_lock_covers_current_manifest_artifacts_and_code():
    lock = json.loads(
        _text("data/metadata/real_v4_ethereum_release_lock.json")
    )
    manifest = json.loads(_text("outputs/real_v4/ethereum/manifest.json"))
    for relative, expected in lock["published_artifacts"].items():
        assert _sha256(relative) == expected
    assert manifest["artifacts"] == {
        relative: expected
        for relative, expected in lock["published_artifacts"].items()
        if not relative.endswith("/manifest.json")
    }
    for relative, expected in manifest["code"].items():
        assert _sha256(relative) == expected


def test_locked_summary_fails_closed_at_the_actor_and_causal_gates():
    summary = json.loads(
        _text("outputs/real_v4/ethereum/summary.json")
    )
    assert summary["event_count"] == 118_806
    assert summary["beneficiary_address_count"] == 15_351
    assert summary["accepted_must_link_constraint_count"] == 0
    assert summary["identified_set_produced"] is True
    assert summary["economic_actor_direction_identified"] is False
    assert summary["entity_level_primary_result_produced"] is False
    assert summary["causal_estimate_produced"] is False
    assert summary["stable_address_actor_hhi_change_lower"] < 0
    assert summary["stable_address_actor_hhi_change_upper"] > 0


def test_public_panel_contains_one_position_holder_address_per_event():
    panel = pd.read_csv(ROOT / "outputs/real_v4/ethereum/beneficiary_event_panel.csv.gz")
    assert list(panel.columns) == [
        "event_ordinal",
        "block_number",
        "event_week",
        "action",
        "beneficiary_address",
    ]
    assert len(panel) == 118_806
    assert panel["event_ordinal"].tolist() == list(range(1, 118_807))
    assert panel["beneficiary_address"].nunique() == 15_351


def test_actor_constraint_release_is_explicitly_empty_not_heuristically_filled():
    constraints = pd.read_csv(
        ROOT / "data/metadata/real_v4_ethereum_actor_constraints.csv"
    )
    assert constraints.empty
    documentation = _text("docs/REAL_V4_PARTIAL_IDENTIFICATION.md")
    for forbidden_basis in (
        "Shared bytecode",
        "transaction similarity",
        "co-timing",
        "common counterparties",
    ):
        assert forbidden_basis in documentation
    assert "not a person" in documentation


def test_manuscript_separates_on_chain_treatment_from_public_changelog():
    timeline = _text("paper/figures/fig02_institutional_timeline.tex")
    causal_section = _text("paper/sections/05_causal_inference.tex")
    assert "On-chain \\GHO{} activation\\\\15 July 2023" in timeline
    assert "changelog records \\GHO{} on 16 July 2023" in timeline
    assert "\\section{Empirical evidence and identification limits}" in causal_section
    assert "no causal coefficient" in causal_section
    assert "prespecified extensions rather than completed findings" in causal_section


def test_manuscript_is_standalone_and_matches_the_longitudinal_scope():
    manuscript = "\n".join(
        _text(path)
        for path in (
            "paper/main.tex",
            "paper/sections/01_introduction.tex",
            "paper/sections/02_background.tex",
            "paper/sections/03_model_simulation.tex",
            "paper/sections/04_data_open_science.tex",
            "paper/sections/05_causal_inference.tex",
            "paper/sections/06_conclusion.tex",
            "paper/tables/tab03_literature_map.tex",
        )
    )
    assert (
        "A longitudinal study of Aave, GHO stablecoin issuance, and cross-chain expansion"
        in manuscript
    )
    for internal_draft_phrase in (
        "This revision",
        "the revision",
        "working draft",
        "final acknowledgement statement will",
        "does not update the abstract",
    ):
        assert internal_draft_phrase not in manuscript


def test_simplified_treatment_registry_matches_the_audited_execution_clocks():
    registry = pd.read_csv(ROOT / "data/metadata/treatment_registry.csv").set_index(
        "event_id"
    )
    ethereum = registry.loc["gho_ethereum_launch_2023"]
    arbitrum = registry.loc["gho_arbitrum_activation_2024"]
    assert "block 17699249" in ethereum["treatment_date_rule"]
    assert "2023-07-15T14:02:59Z" in ethereum["treatment_date_rule"]
    assert "16 July changelog" in ethereum["notes"]
    assert "block 228027379" in arbitrum["treatment_date_rule"]
    assert "2024-07-02T15:40:32Z" in arbitrum["treatment_date_rule"]
    assert set(registry["verification_status"]) == {"Locked A+"}


def test_generated_latex_has_no_control_characters_or_manual_result_drift():
    appendix = _text("paper/appendix/real_v4_partial_identification.tex")
    figure = _text("paper/figures/fig04_real_v4_partial_identification.tex")
    for value in (appendix, figure):
        assert not any(
            ord(character) < 32 and character not in {"\n", "\t"}
            for character in value
        )
    assert "\\label{app:real-v4-partial-identification}" in appendix
    assert "Direction not identified" in figure
    assert "No causal estimate" in figure
