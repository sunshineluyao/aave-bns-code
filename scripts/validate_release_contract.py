#!/usr/bin/env python3
"""Validate the local Aave-BNS cross-repository release contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "release" / "release_contract.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
PRODUCTS = {"data", "code"}
BOUNDARIES = {
    "address_actor",
    "hhi_construct",
    "hhi_aggregation",
    "simulation",
    "causal",
    "infrastructure",
}


def validate(contract: dict) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    products = contract.get("products", {})
    if set(products) != PRODUCTS:
        errors.append(f"products must be exactly {sorted(PRODUCTS)}")
    boundaries = contract.get("evidence_boundaries", {})
    if set(boundaries) != BOUNDARIES:
        errors.append(f"evidence_boundaries must be exactly {sorted(BOUNDARIES)}")
    for name, item in products.items():
        url = item.get("repository", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            errors.append(f"{name}.repository must be an https://github.com URL")
        for key, value in item.items():
            if key.endswith(("commit", "revision")) and not SHA40.fullmatch(str(value)):
                errors.append(f"{name}.{key} must be a 40-character lowercase commit SHA")
    if products.get("data", {}).get("license") != "CC-BY-4.0":
        errors.append("data.license must be CC-BY-4.0")
    if products.get("code", {}).get("license") != "MIT":
        errors.append("code.license must be MIT")
    truth = contract.get("scientific_source", {})
    for key in ("baseline_commit",):
        if not SHA40.fullmatch(truth.get(key, "")):
            errors.append(f"scientific_source.{key} must be a 40-character lowercase commit SHA")
    gates = contract.get("external_publication_gates", [])
    if not isinstance(gates, list) or not gates:
        errors.append("external_publication_gates must remain explicit before Hub publication")
    if gates and contract.get("status") in {"PUBLISHED", "FINAL_RELEASE"}:
        errors.append("status cannot claim publication while external gates remain")
    for key, value in boundaries.items():
        if not isinstance(value, str) or len(value.strip()) < 20:
            errors.append(f"evidence boundary {key} is missing or non-substantive")
    gate = contract.get("reproduction_gate", {})
    for key in (
        "config",
        "entrypoint",
        "result_index",
        "quality_audit",
        "reference",
        "reference_sha256",
        "smoke_fixture",
    ):
        if not gate.get(key):
            errors.append(f"reproduction_gate.{key} is required")
    for key in ("config", "entrypoint", "result_index", "quality_audit", "reference", "smoke_fixture"):
        value = gate.get(key)
        if value and not (ROOT / value).exists():
            errors.append(f"reproduction_gate.{key} does not exist: {value}")
    if gate.get("reference_sha256") and not re.fullmatch(
        r"[0-9a-f]{64}", gate["reference_sha256"]
    ):
        errors.append("reproduction_gate.reference_sha256 must be a lowercase SHA-256")
    if contract.get("final_cross_repository_lock") is not None:
        errors.append("final_cross_repository_lock must remain null until human release approval")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", nargs="?", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    errors = validate(contract)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"PASS: {args.contract} is structurally consistent ({contract['status']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
