from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

REQUIRED = {
    "week", "message_id", "source_chain", "destination_chain", "route_id",
    "amount_wei", "local_activity_wei", "source_tx_hash", "destination_tx_hash",
    "verification_status",
}


def exact_nonnegative(value: str, *, positive: bool = False) -> int:
    if not value or not value.isdigit():
        raise ValueError("numeric provenance must be a base-10 integer string")
    number = int(value)
    if number < 0 or (positive and number == 0):
        raise ValueError("amount violates sign restriction")
    return number


def compute(rows: list[dict[str, str]]) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not rows:
        return [], {"gate_passed": False, "reason": "no_paired_messages"}
    missing = REQUIRED - set(rows[0])
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    message_ids: set[str] = set()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["verification_status"] != "onchain_verified_paired":
            raise ValueError("all messages must be source/destination paired and verified")
        if row["message_id"] in message_ids:
            raise ValueError("duplicate message_id")
        message_ids.add(row["message_id"])
        exact_nonnegative(row["amount_wei"], positive=True)
        exact_nonnegative(row["local_activity_wei"])
        grouped[row["week"]].append(row)

    output: list[dict[str, object]] = []
    for week, week_rows in sorted(grouped.items()):
        route_flows: dict[str, int] = defaultdict(int)
        source_flows: dict[str, int] = defaultdict(int)
        local = max(exact_nonnegative(row["local_activity_wei"]) for row in week_rows)
        for row in week_rows:
            amount = exact_nonnegative(row["amount_wei"], positive=True)
            route_flows[row["route_id"]] += amount
            source_flows[row["source_chain"]] += amount
        cross = sum(route_flows.values())
        shares = [flow / cross for flow in route_flows.values()]
        route_hhi = sum(share * share for share in shares)
        route_count = len(route_flows)
        normalized = (
            (route_hhi - 1 / route_count) / (1 - 1 / route_count)
            if route_count > 1 else 1.0
        )
        output.append({
            "week": week,
            "paired_message_count": len(week_rows),
            "verified_route_count": route_count,
            "cross_chain_flow_wei": cross,
            "local_activity_wei": local,
            "bridge_reliance": cross / (cross + local) if cross + local else None,
            "route_hhi": route_hhi,
            "normalized_route_hhi": normalized,
            "dominant_source_share": max(source_flows.values()) / cross,
            "largest_route_removal_loss": max(route_flows.values()) / cross,
        })
    verified_routes = {row["route_id"] for row in rows}
    return output, {
        "gate_passed": len(verified_routes) >= 2,
        "reason": "passed" if len(verified_routes) >= 2 else "fewer_than_two_verified_routes",
        "paired_message_count": len(rows),
        "verified_route_count": len(verified_routes),
        "infrastructure_result_produced": len(verified_routes) >= 2,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    with Path(args.input).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    metrics, summary = compute(rows)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if metrics:
        with (output / "route_week_metrics.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
            writer.writeheader()
            writer.writerows(metrics)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not summary["gate_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

