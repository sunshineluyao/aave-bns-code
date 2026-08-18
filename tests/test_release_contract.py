import json
from pathlib import Path

from scripts.validate_release_contract import validate


ROOT = Path(__file__).resolve().parents[1]


def load_contract():
    return json.loads((ROOT / "release" / "release_contract.json").read_text(encoding="utf-8"))


def test_release_contract_passes():
    assert validate(load_contract()) == []


def test_ready_is_rejected_while_blocked():
    contract = load_contract()
    contract["status"] = "READY"
    assert any("cannot be READY" in item for item in validate(contract))


def test_scientific_boundaries_are_explicit():
    contract = load_contract()
    assert "not verified natural persons" in contract["evidence_boundaries"]["address_actor"]
    assert "not a treatment effect" in contract["evidence_boundaries"]["causal"]
    assert "not interchangeable" in contract["evidence_boundaries"]["hhi_aggregation"]


def test_reproduction_gate_is_pinned_and_not_overclaimed():
    contract = load_contract()
    gate = contract["reproduction_gate"]
    assert len(gate["reference_sha256"]) == 64
    assert "no new raw-chain full rerun" in gate["validation_ceiling"]
    assert contract["final_cross_repository_lock"] is None
