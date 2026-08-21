import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_causal_v2_donors", ROOT / "scripts/verify_causal_v2_donors.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_earliest_code_block_binary_search():
    def call(method, params):
        assert method == "eth_getCode"
        block = int(params[1], 16)
        return "0x6000" if block >= 137 else "0x"

    assert MODULE.earliest_code_block("0xabc", 1000, call) == 137


def test_earliest_code_block_missing_contract():
    assert (
        MODULE.earliest_code_block(
            "0xabc", 1000, lambda method, params: "0x"
        )
        is None
    )


def test_support_gate_allows_verified_subset():
    gate = MODULE.verification_gate(verified=2, selected=7, minimum_verified=2)
    assert gate["support_audit_eligible"] is True
    assert gate["all_verified"] is False
    assert gate["failed"] == 5


def test_support_gate_blocks_insufficient_subset():
    gate = MODULE.verification_gate(verified=1, selected=7, minimum_verified=2)
    assert gate["support_audit_eligible"] is False

