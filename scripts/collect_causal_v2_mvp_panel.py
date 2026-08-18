from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aave_bns.evm_rpc import (  # noqa: E402
    RpcClient,
    canonicalize_logs,
    int_from_hex,
    resolve_first_block_at_or_after,
)

EVENTS = {
    "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61": ("Supply", 2),
    "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7": ("Withdraw", 2),
    "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0": ("Borrow", 2),
    "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051": ("Repay", 2),
    "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286": ("LiquidationCall", 3),
}

PUBLIC_RPCS = {
    "100": "https://gnosis-rpc.publicnode.com",
    "324": "https://mainnet.era.zksync.io",
    "1088": "https://metis-rpc.publicnode.com",
    "42161": "https://arbitrum-one-rpc.publicnode.com",
    "43114": "https://avalanche-c-chain-rpc.publicnode.com",
    "8453": "https://base-rpc.publicnode.com",
    "534352": "https://scroll-rpc.publicnode.com",
}


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def shard_scan(scan: dict[str, str], shard_index: int, shard_count: int) -> dict[str, str]:
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index must satisfy 0 <= index < count")
    start = parse_utc(scan["scan_start_utc"])
    end = parse_utc(scan["scan_end_utc"])
    if end <= start:
        raise ValueError("scan window must have positive duration")
    duration = end - start
    left = start + duration * shard_index / shard_count
    right = start + duration * (shard_index + 1) / shard_count
    result = dict(scan)
    result["scan_start_utc"] = left.isoformat().replace("+00:00", "Z")
    result["scan_end_utc"] = right.isoformat().replace("+00:00", "Z")
    result["shard_index"] = str(shard_index)
    result["shard_count"] = str(shard_count)
    return result


def address_from_topic(value: str) -> str:
    value = value.lower()
    if not value.startswith("0x") or len(value) != 66:
        raise ValueError(f"malformed indexed address topic: {value}")
    address = "0x" + value[-40:]
    if address == "0x" + "0" * 40:
        raise ValueError("indexed beneficiary resolves to the zero address")
    return address


def fetch_logs_complete(
    client: RpcClient,
    *,
    address: str,
    start_block: int,
    end_block: int,
    initial_width: int = 50_000,
) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    if end_block < start_block:
        return [], []
    pending = [(start_block, min(end_block, start_block + initial_width - 1))]
    next_start = pending[0][1] + 1
    while next_start <= end_block:
        right = min(end_block, next_start + initial_width - 1)
        pending.append((next_start, right))
        next_start = right + 1
    logs: list[dict[str, Any]] = []
    completed: list[dict[str, int]] = []
    cursor = 0
    while cursor < len(pending):
        left, right = pending[cursor]
        cursor += 1
        try:
            batch = client.logs(
                {
                    "address": address,
                    "topics": [list(EVENTS)],
                    "fromBlock": hex(left),
                    "toBlock": hex(right),
                }
            )
        except Exception:
            if left == right:
                raise
            midpoint = (left + right) // 2
            pending[cursor:cursor] = [(left, midpoint), (midpoint + 1, right)]
            continue
        logs.extend(batch)
        completed.append({"from_block": left, "to_block": right, "log_count": len(batch)})
    completed.sort(key=lambda row: row["from_block"])
    expected = start_block
    for row in completed:
        if row["from_block"] != expected:
            raise AssertionError("log coverage contains a gap or overlap")
        expected = row["to_block"] + 1
    if expected != end_block + 1:
        raise AssertionError("log coverage does not reach requested end block")
    return canonicalize_logs(logs), completed


def attach_timestamps(client: RpcClient, logs: list[dict[str, Any]]) -> dict[int, int]:
    blocks = sorted({int_from_hex(row["blockNumber"]) for row in logs})
    timestamps: dict[int, int] = {}
    for offset in range(0, len(blocks), 100):
        chunk = blocks[offset : offset + 100]
        headers = client.batch_call(
            [("eth_getBlockByNumber", [hex(number), False]) for number in chunk]
        )
        for number, header in zip(chunk, headers, strict=True):
            if header is None or int_from_hex(header["number"]) != number:
                raise ValueError(f"missing or mismatched block header {number}")
            timestamps[number] = int_from_hex(header["timestamp"])
    return timestamps


def week_start(timestamp: int) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    monday = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = monday.fromordinal(monday.toordinal() - monday.weekday()).replace(tzinfo=timezone.utc)
    return monday.isoformat().replace("+00:00", "Z")


def aggregate_weekly(events: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in events:
        grouped[(row["chain_id"], row["week_start_utc"], row["event_family"])].append(row)
    output = []
    for (chain_id, week, family), rows in sorted(grouped.items()):
        counts = Counter(row["beneficiary_address"] for row in rows)
        total = len(rows)
        hhi = sum((count / total) ** 2 for count in counts.values()) if total else math.nan
        n = len(counts)
        normalized = (hhi - 1 / n) / (1 - 1 / n) if n > 1 else 1.0
        output.append(
            {
                "chain_id": chain_id,
                "chain": rows[0]["chain"],
                "week_start_utc": week,
                "event_family": family,
                "event_count": str(total),
                "active_beneficiary_addresses": str(n),
                "beneficiary_event_hhi": f"{hhi:.12g}",
                "normalized_beneficiary_event_hhi": f"{normalized:.12g}",
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def collect(scans: list[dict[str, str]], output_dir: Path) -> dict[str, Any]:
    all_events: list[dict[str, str]] = []
    coverage: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for scan in scans:
        chain_id = scan["chain_id"]
        configured_endpoint = os.getenv(scan["rpc_url_env"], "").strip()
        # Avalanche's configured free-tier endpoint returned HTTP 429 during the
        # first bounded run. Prefer the locked public read-only endpoint there;
        # other chains retain their configured endpoint with public fallback.
        endpoint = (
            PUBLIC_RPCS.get(chain_id, "")
            if chain_id == "43114"
            else configured_endpoint or PUBLIC_RPCS.get(chain_id, "")
        )
        if not endpoint:
            failures.append(
                {"chain_id": chain_id, "chain": scan["chain"], "error": "no RPC endpoint"}
            )
            continue
        client = RpcClient(endpoint, timeout_seconds=90, maximum_attempts=4)
        try:
            observed_chain = client.chain_id()
            if observed_chain != int(chain_id):
                raise ValueError(f"chain id mismatch: {observed_chain}")
            latest = client.latest_block_number()
            start = resolve_first_block_at_or_after(
                client, parse_utc(scan["scan_start_utc"]), low_block=0, high_block=latest
            )
            end = resolve_first_block_at_or_after(
                client, parse_utc(scan["scan_end_utc"]), low_block=0, high_block=latest
            )
            start_block, end_block = int(start["start_block"]), int(end["start_block"]) - 1
            logs, chunks = fetch_logs_complete(
                client, address=scan["pool_address"], start_block=start_block, end_block=end_block
            )
            timestamps = attach_timestamps(client, logs)
            seen: set[tuple[str, int]] = set()
            for log in logs:
                topic0 = str(log["topics"][0]).lower()
                if topic0 not in EVENTS:
                    raise ValueError(f"unexpected event topic {topic0}")
                family, beneficiary_index = EVENTS[topic0]
                key = (str(log["transactionHash"]).lower(), int_from_hex(log["logIndex"]))
                if key in seen:
                    raise ValueError(f"duplicate log {key}")
                seen.add(key)
                block_number = int_from_hex(log["blockNumber"])
                timestamp = timestamps[block_number]
                all_events.append(
                    {
                        "chain_id": chain_id,
                        "chain": scan["chain"],
                        "pool_address": scan["pool_address"].lower(),
                        "block_number": str(block_number),
                        "block_timestamp_utc": datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "week_start_utc": week_start(timestamp),
                        "transaction_hash": str(log["transactionHash"]).lower(),
                        "log_index": str(int_from_hex(log["logIndex"])),
                        "event_family": family,
                        "beneficiary_address": address_from_topic(log["topics"][beneficiary_index]),
                    }
                )
            coverage.append(
                {
                    "chain_id": chain_id,
                    "chain": scan["chain"],
                    "start_block": start_block,
                    "end_block": end_block,
                    "chunk_count": len(chunks),
                    "event_count": len(logs),
                    "coverage_complete": "true",
                    "rpc_requests": client.stats.requests,
                    "analysis_ids": scan["analysis_ids"],
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "chain_id": chain_id,
                    "chain": scan["chain"],
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    event_fields = [
        "chain_id",
        "chain",
        "pool_address",
        "block_number",
        "block_timestamp_utc",
        "week_start_utc",
        "transaction_hash",
        "log_index",
        "event_family",
        "beneficiary_address",
    ]
    panel = aggregate_weekly(all_events)
    write_csv(output_dir / "pool_events.csv", all_events, event_fields)
    write_csv(
        output_dir / "weekly_panel.csv",
        panel,
        [
            "chain_id",
            "chain",
            "week_start_utc",
            "event_family",
            "event_count",
            "active_beneficiary_addresses",
            "beneficiary_event_hhi",
            "normalized_beneficiary_event_hhi",
        ],
    )
    write_csv(
        output_dir / "coverage.csv",
        coverage,
        [
            "chain_id",
            "chain",
            "start_block",
            "end_block",
            "chunk_count",
            "event_count",
            "coverage_complete",
            "rpc_requests",
            "analysis_ids",
        ],
    )
    write_csv(output_dir / "failures.csv", failures, ["chain_id", "chain", "error"])
    summary = {
        "schema_version": 1,
        "design": "causal-v2-mvp-pool-event-panel",
        "requested_scans": len(scans),
        "completed_scans": len(coverage),
        "failed_scans": len(failures),
        "event_rows": len(all_events),
        "weekly_panel_rows": len(panel),
        "panel_complete": len(scans) > 0 and not failures and len(coverage) == len(scans),
        "att_gate_open": False,
        "claim_boundary": (
            "Pool-event acquisition only; ATT remains closed pending panel QA and "
            "estimator tests."
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scans", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chain-id", help="Collect exactly one approved chain from the manifest")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()
    with args.scans.open(encoding="utf-8", newline="") as stream:
        scans = list(csv.DictReader(stream))
    if args.chain_id:
        scans = [row for row in scans if row["chain_id"] == args.chain_id]
        if len(scans) != 1:
            raise ValueError(
                f"expected exactly one approved scan for chain {args.chain_id}; found {len(scans)}"
            )
    if not scans:
        raise ValueError("refusing empty acquisition manifest")
    scans = [shard_scan(row, args.shard_index, args.shard_count) for row in scans]
    summary = collect(scans, args.output_dir)
    summary["shard_index"] = args.shard_index
    summary["shard_count"] = args.shard_count
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, sort_keys=True))
    if not summary["panel_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
