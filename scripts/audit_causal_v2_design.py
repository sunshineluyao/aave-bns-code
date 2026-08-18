from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def audit() -> dict[str, object]:
    treatments = read_csv(ROOT / "data/metadata/causal_v2_treatment_registry.csv")
    donors = read_csv(ROOT / "data/metadata/causal_v2_donor_registry.csv")
    routes = read_csv(ROOT / "data/metadata/bridge_v2_route_registry.csv")

    errors: list[str] = []
    if len({row["cohort_id"] for row in treatments}) != len(treatments):
        errors.append("duplicate cohort_id")
    for row in treatments:
        commitment = parse_utc(row["formal_commitment_utc"])
        activation = parse_utc(row["operational_activation_utc"])
        market = parse_utc(row["market_available_by_utc"])
        bundled = row["bundled_market_entry"].lower() == "true"
        if commitment >= activation:
            errors.append(f'{row["cohort_id"]}: commitment must precede activation')
        if bundled != (market == activation):
            errors.append(f'{row["cohort_id"]}: bundled flag does not match market clock')
        if not row["formal_commitment_source"].startswith("https://"):
            errors.append(f'{row["cohort_id"]}: missing primary-source URL')

    verified_donors = [
        row for row in donors if row["verification_status"] == "verified"
    ]
    historical_routes = [
        row for row in routes if row["configuration_status"] == "historically_verified"
    ]
    present_routes = [
        row for row in routes if row["configuration_status"] == "current_verified"
    ]
    bridge_gate = len(historical_routes) >= 2 or len(present_routes) >= 2
    staggered_gate = len(verified_donors) >= 2 and len(treatments) >= 3

    return {
        "schema_version": 1,
        "status": "invalid" if errors else "design_valid_evidence_pending",
        "errors": errors,
        "treatment_cohort_count": len(treatments),
        "candidate_donor_count": len(donors),
        "verified_donor_count": len(verified_donors),
        "historically_verified_route_count": len(historical_routes),
        "current_verified_route_count": len(present_routes),
        "formal_commitment_staggered_did_gate": staggered_gate,
        "bridge_metric_gate": bridge_gate,
        "causal_estimate_produced": False,
        "infrastructure_result_produced": False,
        "next_actions": [
            "verify donor market clocks and freeze Pool addresses",
            "acquire only supported chain-reserve-action-week windows",
            "resolve historical CCIP route validity intervals",
            "pair source and destination messages before computing bridge metrics",
        ],
    }


def main() -> None:
    result = audit()
    output = ROOT / "outputs/causal_v2/design_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

