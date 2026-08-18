from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aave_bns.ccip_route_extract import canonical_hash
from aave_bns.evm_rpc import RpcClient, canonicalize_logs, int_from_hex


def _require_hex(value: str, byte_length: int, name: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 2 + 2 * byte_length:
        raise ValueError(f"{name} is malformed")
    try:
        decoded = bytes.fromhex(normalized[2:])
    except ValueError as exc:
        raise ValueError(f"{name} is malformed") from exc
    if len(decoded) != byte_length:
        raise ValueError(f"{name} is malformed")
    return normalized


@dataclass(frozen=True)
class EventQuery:
    chain_id: int
    contract_address: str
    topic0: str
    start_block: int
    end_block: int
    message_id_topic_index: int
    chunk_size: int = 2_000

    def validate(self) -> None:
        if self.start_block < 0 or self.end_block < self.start_block:
            raise ValueError("invalid inclusive block interval")
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if self.message_id_topic_index < 1:
            raise ValueError("message_id must be an indexed topic, not topic0")
        _require_hex(self.contract_address, 20, "contract_address")
        _require_hex(self.topic0, 32, "topic0")


def _validate_returned_log(
    log: dict[str, Any], query: EventQuery, chunk_start: int, chunk_end: int
) -> None:
    if log.get("removed") is not False:
        raise ValueError("RPC log removed must be present and strictly false")
    if str(log.get("address", "")).lower() != query.contract_address.lower():
        raise ValueError("RPC returned a log from a different contract")
    topics = log.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("RPC returned a log without topic0")
    if str(topics[0]).lower() != query.topic0.lower():
        raise ValueError("RPC returned a log with a different topic0")
    block_number = int_from_hex(log.get("blockNumber", ""))
    if not chunk_start <= block_number <= chunk_end:
        raise ValueError("RPC returned a log outside the requested chunk")


def _decode(log: dict[str, Any], query: EventQuery) -> dict[str, Any]:
    topics = log.get("topics", [])
    if len(topics) <= query.message_id_topic_index:
        raise ValueError("configured message_id topic is absent")
    message_id = _require_hex(
        str(topics[query.message_id_topic_index]), 32, "message_id topic"
    )
    return {
        "chain_id": query.chain_id,
        "contract_address": str(log["address"]).lower(),
        "message_id": message_id,
        "tx_hash": str(log["transactionHash"]).lower(),
        "block_hash": str(log["blockHash"]).lower(),
        "block_number": int_from_hex(log["blockNumber"]),
        "transaction_index": int_from_hex(log.get("transactionIndex", "0x0")),
        "log_index": int_from_hex(log["logIndex"]),
        "removed": False,
    }


def extract_indexed_message_events(
    client: RpcClient, query: EventQuery
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract one pinned event type; fail closed on identity, bytecode, or coverage errors."""
    query.validate()
    if client.chain_id() != query.chain_id:
        raise ValueError("RPC chain_id does not match the pinned query")
    start_code = client.code(query.contract_address, query.start_block)
    end_code = client.code(query.contract_address, query.end_block)
    if start_code == "0x" or end_code == "0x":
        raise ValueError("contract has no historical runtime code at a pinned boundary")

    raw: list[dict[str, Any]] = []
    covered: list[tuple[int, int]] = []
    cursor = query.start_block
    while cursor <= query.end_block:
        chunk_end = min(query.end_block, cursor + query.chunk_size - 1)
        returned = client.logs(
            {
                "address": query.contract_address,
                "fromBlock": hex(cursor),
                "toBlock": hex(chunk_end),
                "topics": [query.topic0],
            }
        )
        for log in returned:
            _validate_returned_log(log, query, cursor, chunk_end)
        raw.extend(returned)
        covered.append((cursor, chunk_end))
        cursor = chunk_end + 1

    normalized = canonicalize_logs(raw)
    events = [_decode(log, query) for log in normalized]
    identities = [(row["tx_hash"], row["log_index"]) for row in events]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate transaction/log identity")
    audit = {
        "schema_version": 2,
        "chain_id": query.chain_id,
        "contract_address": query.contract_address.lower(),
        "topic0": query.topic0.lower(),
        "start_block": query.start_block,
        "end_block": query.end_block,
        "covered_ranges": covered,
        "continuous_inclusive_coverage": bool(covered)
        and covered[0][0] == query.start_block
        and covered[-1][1] == query.end_block
        and all(a[1] + 1 == b[0] for a, b in zip(covered, covered[1:])),
        "returned_log_validation_passed": True,
        "historical_code_start_sha256": canonical_hash([{"code": start_code}]),
        "historical_code_end_sha256": canonical_hash([{"code": end_code}]),
        "raw_log_count": len(normalized),
        "decoded_event_count": len(events),
        "events_sha256": canonical_hash(events),
        "rpc_stats": client.stats.to_dict(),
        "exhaustive_prior_scan_verified": False,
        "first_transfer_claim_permitted": False,
        "bridge_route_gate_passed": False,
        "infrastructure_dependence_result_produced": False,
    }
    return events, audit


def write_extraction_artifacts(
    client: RpcClient, query: EventQuery, output_dir: str | Path
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    events, audit = extract_indexed_message_events(client, query)
    event_path = output / "decoded_events.json"
    audit_path = output / "extraction_manifest.json"
    event_path.write_text(
        json.dumps(events, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    audit_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return event_path, audit_path
