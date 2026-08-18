from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def canonical_hash(records: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(list(records), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def pair_message_events(
    source_events: list[dict[str, Any]], destination_events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair already-decoded CCIP events without guessing missing message identities."""
    destination_by_id: dict[str, dict[str, Any]] = {}
    duplicate_destination_ids: set[str] = set()
    for event in destination_events:
        message_id = event.get("message_id")
        if not message_id:
            continue
        if message_id in destination_by_id:
            duplicate_destination_ids.add(message_id)
        destination_by_id[message_id] = event

    paired: list[dict[str, Any]] = []
    unmatched_source_ids: list[str] = []
    for source in source_events:
        message_id = source.get("message_id")
        destination = destination_by_id.get(message_id)
        if not message_id or destination is None or message_id in duplicate_destination_ids:
            if message_id:
                unmatched_source_ids.append(message_id)
            continue
        paired.append(
            {
                **source,
                "destination_tx_hash": destination.get("tx_hash"),
                "destination_log_index": destination.get("log_index"),
                "destination_block": destination.get("block_number"),
            }
        )

    audit = {
        "schema_version": 1,
        "source_event_count": len(source_events),
        "destination_event_count": len(destination_events),
        "paired_event_count": len(paired),
        "unmatched_source_count": len(unmatched_source_ids),
        "duplicate_destination_message_ids": sorted(duplicate_destination_ids),
        "source_sha256": canonical_hash(source_events),
        "destination_sha256": canonical_hash(destination_events),
        "paired_sha256": canonical_hash(paired),
        "exhaustive_prior_scan_verified": False,
        "first_transfer_claim_permitted": False,
        "bridge_route_gate_passed": False,
        "infrastructure_dependence_result_produced": False,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }
    return paired, audit


def write_pairing_artifacts(
    source_events: list[dict[str, Any]],
    destination_events: list[dict[str, Any]],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paired, audit = pair_message_events(source_events, destination_events)
    paired_path = output / "paired_ccip_messages.json"
    audit_path = output / "pairing_manifest.json"
    paired_path.write_text(json.dumps(paired, indent=2, sort_keys=True) + "\n")
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return paired_path, audit_path
