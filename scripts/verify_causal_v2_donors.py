from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data/metadata/causal_v2_donor_registry.csv"
OUT_DIR = ROOT / "outputs/causal_v2/donor_preflight"


class RpcError(RuntimeError):
    pass


def rpc_call(url: str, method: str, params: list[object], attempts: int = 3) -> object:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "aave-bns-causal-v2-preflight/1.1",
    }
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                body = json.load(response)
            if body.get("error"):
                raise RpcError(str(body["error"]))
            return body["result"]
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RpcError(str(last))


def earliest_code_block(
    address: str,
    latest: int,
    call: Callable[[str, list[object]], object],
) -> int | None:
    if call("eth_getCode", [address, hex(latest)]) in ("0x", "0x0", None):
        return None
    low, high = 0, latest
    while low < high:
        mid = (low + high) // 2
        code = call("eth_getCode", [address, hex(mid)])
        if code in ("0x", "0x0", None):
            low = mid + 1
        else:
            high = mid
    return low


def verification_gate(verified: int, selected: int, minimum_verified: int) -> dict[str, object]:
    if minimum_verified < 1:
        raise ValueError("minimum_verified must be positive")
    return {
        "selected": selected,
        "verified": verified,
        "failed": selected - verified,
        "minimum_verified": minimum_verified,
        "support_audit_eligible": verified >= minimum_verified,
        "all_verified": verified == selected,
    }


def verify_row(row: dict[str, str]) -> dict[str, object]:
    env_url = os.getenv(row["rpc_url_env"], "") if row["rpc_url_env"] else ""
    url = env_url or row["public_rpc_url"]
    result: dict[str, object] = {
        "market_id": row["market_id"],
        "chain": row["chain"],
        "chain_id": int(row["chain_id"]),
        "pool_address": row["pool_address"],
        "rpc_source": "secret" if env_url else "public_fallback",
        "status": "failed",
        "latest_block": None,
        "pool_first_code_block": None,
        "pool_first_code_timestamp_utc": "",
        "error": "",
    }
    if not url:
        result["error"] = "no RPC URL configured"
        return result
    try:
        call = lambda method, params: rpc_call(url, method, params)
        observed_chain = int(str(call("eth_chainId", [])), 16)
        if observed_chain != int(row["chain_id"]):
            raise RpcError(
                f"chain id mismatch: expected {row['chain_id']}, observed {observed_chain}"
            )
        latest = int(str(call("eth_blockNumber", [])), 16)
        first = earliest_code_block(row["pool_address"], latest, call)
        if first is None:
            raise RpcError("Pool has no code at latest block")
        first_header = call("eth_getBlockByNumber", [hex(first), False])
        first_timestamp = datetime.fromtimestamp(
            int(str(first_header["timestamp"]), 16), tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
        result.update(
            status="verified",
            latest_block=latest,
            pool_first_code_block=first,
            pool_first_code_timestamp_utc=first_timestamp,
        )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--market",
        action="append",
        help="Bound the run to one or more market_id values",
    )
    parser.add_argument(
        "--minimum-verified",
        type=int,
        default=2,
        help="Minimum verified donors needed to enter the cohort support audit",
    )
    args = parser.parse_args()
    with REGISTRY.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if args.market:
        wanted = set(args.market)
        rows = [row for row in rows if row["market_id"] in wanted]
    if not rows:
        raise SystemExit("no donor rows selected")
    results = [verify_row(row) for row in rows]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = list(results[0])
    with (OUT_DIR / "donor_verification.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    verified = sum(row["status"] == "verified" for row in results)
    summary = {
        "schema_version": 2,
        **verification_gate(verified, len(results), args.minimum_verified),
        "gate_scope": (
            "Preflight eligibility only. Each failed market remains excluded; "
            "cohort-specific contemporaneous support is evaluated downstream."
        ),
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    if not summary["support_audit_eligible"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
