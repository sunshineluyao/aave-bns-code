from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_support(
    treatments: list[dict[str, str]],
    donor_evidence: list[dict[str, str]],
    window_weeks: int = 16,
    minimum_donors: int = 2,
    minimum_cohorts: int = 3,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    verified = [
        row for row in donor_evidence
        if row["status"] == "verified" and row.get("pool_first_code_timestamp_utc")
    ]
    support: list[dict[str, object]] = []
    for treatment in treatments:
        bundled = treatment["bundled_market_entry"].lower() == "true"
        for clock, field in (
            ("formal_commitment", "formal_commitment_utc"),
            ("operational_activation", "operational_activation_utc"),
        ):
            event = parse_utc(treatment[field])
            clean_pre_start = event - timedelta(weeks=window_weeks)
            eligible = sorted(
                row["market_id"]
                for row in verified
                if parse_utc(row["pool_first_code_timestamp_utc"]) <= clean_pre_start
                and int(row["chain_id"]) != int(treatment["chain_id"])
            )
            gate = len(eligible) >= minimum_donors and not bundled
            support.append(
                {
                    "cohort_id": treatment["cohort_id"],
                    "chain": treatment["chain"],
                    "clock": clock,
                    "event_utc": treatment[field],
                    "clean_pre_start_utc": clean_pre_start.isoformat().replace("+00:00", "Z"),
                    "window_weeks": window_weeks,
                    "eligible_donor_count": len(eligible),
                    "eligible_market_ids": ";".join(eligible),
                    "bundled_market_entry": str(bundled).lower(),
                    "support_gate": str(gate).lower(),
                }
            )
    primary = [
        row for row in support
        if row["clock"] == "formal_commitment" and row["support_gate"] == "true"
    ]
    secondary = [
        row for row in support
        if row["clock"] == "operational_activation" and row["support_gate"] == "true"
    ]
    summary = {
        "schema_version": 1,
        "design": "causal-v2-mvp-support",
        "verified_donors": len(verified),
        "minimum_contemporaneous_donors": minimum_donors,
        "minimum_treated_cohorts": minimum_cohorts,
        "window_weeks": window_weeks,
        "formal_commitment_supported_cohorts": [row["cohort_id"] for row in primary],
        "operational_activation_supported_cohorts": [row["cohort_id"] for row in secondary],
        "formal_commitment_mvp_gate": len(primary) >= minimum_cohorts,
        "operational_activation_mvp_gate": len(secondary) >= minimum_cohorts,
        "causal_estimate_produced": False,
        "claim_boundary": "Support eligibility only; no ATT is estimated by this audit.",
    }
    return support, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--treatments", type=Path, required=True)
    parser.add_argument("--donor-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--window-weeks", type=int, default=16)
    args = parser.parse_args()
    with args.treatments.open(encoding="utf-8", newline="") as stream:
        treatments = list(csv.DictReader(stream))
    with args.donor_evidence.open(encoding="utf-8", newline="") as stream:
        donors = list(csv.DictReader(stream))
    rows, summary = build_support(treatments, donors, window_weeks=args.window_weeks)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "cohort_support.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))
    if not summary["formal_commitment_mvp_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
