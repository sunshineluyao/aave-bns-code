from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fmt_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_manifest(
    support_rows: list[dict[str, str]],
    treatments: list[dict[str, str]],
    treated_markets: list[dict[str, str]],
    donor_registry: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    treatment_by_cohort = {row["cohort_id"]: row for row in treatments}
    treated_by_chain_id = {row["chain_id"]: row for row in treated_markets}
    donors_by_id = {row["market_id"]: row for row in donor_registry}
    jobs: list[dict[str, str]] = []

    for support in support_rows:
        if support["support_gate"].lower() != "true":
            continue
        treatment = treatment_by_cohort[support["cohort_id"]]
        treated = treated_by_chain_id.get(treatment["chain_id"])
        if treated is None or treated.get("verification_status") != "verified_locked_registry":
            raise ValueError(f"missing verified treated Pool for {support['cohort_id']}")
        event = parse_utc(support["event_utc"])
        weeks = int(support["window_weeks"])
        start, end = event - timedelta(weeks=weeks), event + timedelta(weeks=weeks)
        analysis_id = f"{support['cohort_id']}__{support['clock']}"
        units = [("treated", treatment["cohort_id"], treated)]
        for donor_id in filter(None, support["eligible_market_ids"].split(";")):
            donor = donors_by_id.get(donor_id)
            if donor is None:
                raise ValueError(f"support matrix references unknown donor {donor_id}")
            units.append(("donor", donor_id, donor))
        if sum(role == "donor" for role, _, _ in units) < 2:
            raise ValueError(f"{analysis_id} has fewer than two donors")
        for role, market_id, market in units:
            jobs.append({
                "analysis_id": analysis_id,
                "clock": support["clock"],
                "role": role,
                "cohort_id": treatment["cohort_id"],
                "market_id": market_id,
                "chain": market["chain"],
                "chain_id": market["chain_id"],
                "pool_address": market["pool_address"],
                "rpc_url_env": market["rpc_url_env"],
                "event_utc": support["event_utc"],
                "window_start_utc": fmt_utc(start),
                "window_end_utc": fmt_utc(end),
                "window_weeks": str(weeks),
                "event_families": "Supply;Withdraw;Borrow;Repay;LiquidationCall",
                "primary_sample": "true" if support["clock"] == "formal_commitment" else "false",
            })

    scans_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for job in jobs:
        key = (job["chain_id"], job["pool_address"].lower())
        current = scans_by_key.get(key)
        if current is None:
            scans_by_key[key] = {
                "chain": job["chain"],
                "chain_id": job["chain_id"],
                "pool_address": job["pool_address"],
                "rpc_url_env": job["rpc_url_env"],
                "scan_start_utc": job["window_start_utc"],
                "scan_end_utc": job["window_end_utc"],
                "analysis_ids": job["analysis_id"],
                "event_families": job["event_families"],
            }
        else:
            current["scan_start_utc"] = min(current["scan_start_utc"], job["window_start_utc"])
            current["scan_end_utc"] = max(current["scan_end_utc"], job["window_end_utc"])
            current["analysis_ids"] = ";".join(sorted(set(current["analysis_ids"].split(";")) | {job["analysis_id"]}))

    scans = sorted(scans_by_key.values(), key=lambda row: (int(row["chain_id"]), row["pool_address"]))
    formal = sorted({row["cohort_id"] for row in jobs if row["clock"] == "formal_commitment"})
    activation = sorted({row["cohort_id"] for row in jobs if row["clock"] == "operational_activation"})
    summary = {
        "schema_version": 1,
        "design": "causal-v2-mvp-acquisition-manifest",
        "analysis_count": len({row["analysis_id"] for row in jobs}),
        "unit_job_count": len(jobs),
        "deduplicated_chain_scans": len(scans),
        "formal_commitment_cohorts": formal,
        "operational_activation_cohorts": activation,
        "minimum_donors_per_analysis": min(
            (sum(row["analysis_id"] == aid and row["role"] == "donor" for row in jobs)
             for aid in {row["analysis_id"] for row in jobs}),
            default=0,
        ),
        "pool_events_only": True,
        "acquisition_executed": False,
        "causal_estimate_produced": False,
        "claim_boundary": "Manifest only; no logs, panel rows, or ATT are produced.",
    }
    if len(formal) < 3 or summary["minimum_donors_per_analysis"] < 2:
        raise ValueError("MVP acquisition manifest violates frozen causal gates")
    return jobs, scans, summary


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty manifest: {path}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--treatments", type=Path, required=True)
    parser.add_argument("--treated-markets", type=Path, required=True)
    parser.add_argument("--donors", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    jobs, scans, summary = build_manifest(
        read_csv(args.support), read_csv(args.treatments),
        read_csv(args.treated_markets), read_csv(args.donors),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "analysis_unit_jobs.csv", jobs)
    write_csv(args.output_dir / "deduplicated_scan_windows.csv", scans)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
