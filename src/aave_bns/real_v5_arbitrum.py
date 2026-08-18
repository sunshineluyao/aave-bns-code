from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from .aave_v3_events import EVENT_SPECS, decode_pool_log, event_topics
from .config import load_yaml
from .evm_rpc import (
    RpcClient,
    RpcError,
    RpcStats,
    canonical_json_sha256,
    canonicalize_logs,
    int_from_hex,
)
from .provenance import sha256_file, write_manifest
from .real_v2 import parse_utc
from .real_v2_ethereum import (
    BlockChunk,
    _can_split_log_query,
    _write_processed_events,
    assign_event_weeks,
    boundary_targets,
    build_chunks,
    build_reserve_week_action_panel,
    build_weekly_action_panel,
    cross_provider_boundary_checks,
    fetch_log_chunks,
    gzip_payload_sha256,
    project_relative_path,
    read_and_validate_boundary_cache,
    read_cohort_calendar,
    resolve_boundaries,
    safe_rpc_endpoint,
    source_revision,
    validate_chunk_logs,
    validation_windows,
    write_csv_records,
)

CONSENSUS_LOG_FIELDS = (
    "address",
    "topics",
    "data",
    "blockHash",
    "blockNumber",
    "transactionHash",
    "transactionIndex",
    "logIndex",
    "removed",
)
LOG_COMPARISON_SCHEMA = "eth_getLogs-consensus-v1"


def _canonical_json_list_sha256(values: list[dict[str, Any]]) -> str:
    """Hash a canonical JSON list without materializing the full serialized payload."""
    digest = hashlib.sha256()
    digest.update(b"[")
    for index, value in enumerate(values):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
    digest.update(b"]")
    return digest.hexdigest()


def load_real_v5_config(
    path: str | Path = "configs/real_v5_arbitrum.yaml",
) -> dict[str, Any]:
    config = load_yaml(path)
    if not isinstance(config, dict) or int(config.get("schema_version", 0)) != 1:
        raise ValueError("Arbitrum real_v5 extraction requires schema_version 1")
    if int(config["chain"]["chain_id"]) != 42161:
        raise ValueError("The Arbitrum extractor requires chain_id 42161")
    configured = {
        str(row["action"]): (str(row["signature"]), str(row["topic0"]).lower())
        for row in config["events"]
    }
    expected = {spec.action: (spec.signature, spec.topic0) for spec in EVENT_SPECS}
    if configured != expected:
        raise ValueError("Configured Aave event signatures or topics do not match the decoder")
    if not str(config["chain"]["primary_rpc"].get("environment_variable", "")):
        raise ValueError(
            "The primary Arbitrum RPC must be supplied through an environment variable"
        )
    if int(config["retrieval"]["maximum_blocks_per_request"]) > 10_000:
        raise ValueError("real_v5 fixes RPC log partitions at no more than 10,000 blocks")
    retrieval = config["retrieval"]
    cache_width = int(retrieval["maximum_blocks_per_request"])
    query_width = int(retrieval["initial_blocks_per_log_query"])
    minimum_query_width = int(retrieval["minimum_adaptive_blocks_per_log_query"])
    minimum_bulk_width = int(retrieval["minimum_viable_bulk_blocks_per_query"])
    workers = int(retrieval["log_workers"])
    maximum_pending = int(retrieval["maximum_pending_log_chunks"])
    if not 1 <= query_width <= cache_width:
        raise ValueError("initial_blocks_per_log_query must be within the cache chunk width")
    if not 1 <= minimum_query_width <= minimum_bulk_width <= query_width:
        raise ValueError(
            "real_v5 query widths must satisfy 1 <= adaptive minimum <= bulk minimum "
            "<= initial width"
        )
    probe_widths = [int(value) for value in retrieval["bulk_log_probe_widths"]]
    if (
        not probe_widths
        or probe_widths != sorted(set(probe_widths), reverse=True)
        or any(width < minimum_bulk_width or width > query_width for width in probe_widths)
    ):
        raise ValueError(
            "bulk_log_probe_widths must be unique, descending, and within the viable range"
        )
    provider_order = [str(value) for value in retrieval["bulk_log_provider_order"]]
    if sorted(provider_order) != ["primary_rpc", "validation_rpc"]:
        raise ValueError(
            "bulk_log_provider_order must contain primary_rpc and validation_rpc exactly once"
        )
    if workers < 1 or maximum_pending < workers:
        raise ValueError("real_v5 log concurrency must have pending chunks >= workers >= 1")
    if int(retrieval["progress_every_chunks"]) < 1:
        raise ValueError("progress_every_chunks must be positive")
    if float(retrieval["progress_interval_seconds"]) <= 0:
        raise ValueError("progress_interval_seconds must be positive")
    if float(retrieval["maximum_runtime_seconds"]) <= 0:
        raise ValueError("maximum_runtime_seconds must be positive")
    return config


def _required_rpc_url(specification: dict[str, Any]) -> str:
    variable = str(specification["environment_variable"])
    value = os.getenv(variable, "").strip()
    if not value:
        raise ValueError(f"Required GitHub Actions secret {variable} is not available")
    return _validated_rpc_url(value, label=variable)


def _validation_rpc_url(specification: dict[str, Any]) -> str:
    variable = str(specification.get("environment_variable", ""))
    override = os.getenv(variable, "").strip() if variable else ""
    return _validated_rpc_url(override or str(specification["url"]), label="validation RPC")


def _validated_rpc_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(
            f"{label} must contain the complete http(s) RPC URL; an API key alone is insufficient"
        )
    return value


def _independent_endpoint(primary_url: str, validation_url: str) -> bool:
    return safe_rpc_endpoint(primary_url) != safe_rpc_endpoint(validation_url)


class _RedactingRpcClient(RpcClient):
    """Keep credential-bearing RPC URLs out of exception text and CI logs."""

    def _redacted_error(self, error: RpcError) -> RpcError:
        return RpcError(str(error).replace(self.url, safe_rpc_endpoint(self.url)))

    def call(self, method: str, params: list[Any]) -> Any:
        try:
            return super().call(method, params)
        except RpcError as error:
            raise self._redacted_error(error) from error

    def batch_call(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        try:
            return super().batch_call(calls)
        except RpcError as error:
            raise self._redacted_error(error) from error

    def logs_once(self, filter_parameters: dict[str, Any]) -> list[dict[str, Any]]:
        """Make one redacted log request for adaptive range or pacing control."""
        one_shot = RpcClient(
            self.url,
            timeout_seconds=self.timeout_seconds,
            maximum_attempts=1,
            stats=self.stats,
            post=self._post,
            sleep=self._sleep,
        )
        try:
            return one_shot.logs(filter_parameters)
        except RpcError as error:
            raise self._redacted_error(error) from error

    def logs(self, filter_parameters: dict[str, Any]) -> list[dict[str, Any]]:
        """Probe once so deterministic range errors can be bisected without six retries."""
        try:
            return self.logs_once(filter_parameters)
        except RpcError as error:
            if _can_split_log_query(error):
                raise
        return super().logs(filter_parameters)


@dataclass(frozen=True)
class _BulkLogSelection:
    role: str
    source_id: str
    client: RpcClient
    crosscheck_role: str
    crosscheck_source_id: str
    crosscheck_client: RpcClient
    query_width: int


class _PacedLogClient:
    """Serialize request starts without changing the locked generic RPC client."""

    def __init__(
        self,
        client: RpcClient,
        *,
        minimum_interval_seconds: float,
        rate_limit_cooldown_seconds: float = 65.0,
        maximum_rate_limit_cooldowns: int = 8,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        if minimum_interval_seconds <= 0:
            raise ValueError("minimum_interval_seconds must be positive")
        if rate_limit_cooldown_seconds <= 0 or maximum_rate_limit_cooldowns < 1:
            raise ValueError("Rate-limit cooldown settings must be positive")
        self.client = client
        self.minimum_interval_seconds = minimum_interval_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.maximum_rate_limit_cooldowns = maximum_rate_limit_cooldowns
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._cooldown_until = 0.0

    def _wait_for_request_slot(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                delay = max(self._next_start, self._cooldown_until) - now
                if delay <= 0:
                    self._next_start = now + self.minimum_interval_seconds
                    return
            # Sleeping outside the lock lets every worker observe the same cooldown.
            # On wake, workers compete again and only one reserves the next start slot.
            self._sleep(delay)

    def _start_shared_cooldown(self) -> bool:
        """Start one cooldown across workers; return whether this caller started it."""
        with self._lock:
            now = self._clock()
            if self._cooldown_until > now:
                return False
            self._cooldown_until = now + self.rate_limit_cooldown_seconds
            return True

    @staticmethod
    def _is_rate_limited(error: RpcError) -> bool:
        message = str(error).lower()
        return "429" in message or any(
            marker in message
            for marker in ("rate limit", "too many requests", "capacity", "quota")
        )

    @staticmethod
    def _is_transient_transport_failure(error: RpcError) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "502",
                "503",
                "504",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
                "timed out",
                "timeout",
                "connection aborted",
                "connection reset",
                "temporarily unavailable",
            )
        )

    def logs(self, filter_parameters: dict[str, Any]) -> list[dict[str, Any]]:
        for cooldown_index in range(self.maximum_rate_limit_cooldowns + 1):
            self._wait_for_request_slot()
            try:
                request = getattr(self.client, "logs_once", self.client.logs)
                return request(filter_parameters)
            except RpcError as error:
                if self._is_rate_limited(error):
                    if cooldown_index == self.maximum_rate_limit_cooldowns:
                        raise
                    if self._start_shared_cooldown():
                        print(
                            "bulk log source rate-limited; starting shared "
                            f"{self.rate_limit_cooldown_seconds:g}-second cooldown",
                            flush=True,
                        )
                    continue
                if self._is_transient_transport_failure(error):
                    if cooldown_index == self.maximum_rate_limit_cooldowns:
                        raise
                    delay = min(45.0, 5.0 * (2**cooldown_index))
                    print(
                        "bulk log source transient failure; retrying same request in "
                        f"{delay:g} seconds",
                        flush=True,
                    )
                    self._sleep(delay)
                    continue
                raise
        raise AssertionError("unreachable rate-limit retry loop")

@dataclass(frozen=True)
class _BulkLogRuntime:
    client: Any
    workers: int
    maximum_pending: int
    minimum_interval_seconds: float
    rate_limit_cooldown_seconds: float


def _bulk_log_runtime(
    selection: _BulkLogSelection, retrieval: dict[str, Any]
) -> _BulkLogRuntime:
    workers = int(retrieval["log_workers"])
    maximum_pending = int(retrieval["maximum_pending_log_chunks"])
    if selection.source_id != "arbitrum_official_rpc":
        return _BulkLogRuntime(selection.client, workers, maximum_pending, 0.0, 0.0)

    # The official endpoint is explicitly a best-effort, low-volume service. One globally
    # paced request start per second keeps the 7,927-query scan below its observed burst
    # limit while two workers overlap response latency.
    interval = 1.0
    cooldown = 65.0
    workers = min(workers, 2)
    maximum_pending = min(maximum_pending, max(workers, 4))
    return _BulkLogRuntime(
        _PacedLogClient(
            selection.client,
            minimum_interval_seconds=interval,
            rate_limit_cooldown_seconds=cooldown,
        ),
        workers,
        maximum_pending,
        interval,
        cooldown,
    )


def _select_bulk_log_source(
    *,
    chain: dict[str, Any],
    retrieval: dict[str, Any],
    primary: RpcClient,
    validation: RpcClient,
    pool_address: str,
    topics: list[str],
    probe_start_block: int,
    maximum_block: int,
) -> _BulkLogSelection:
    """Select a provider that can cover the panel without millions of requests."""
    clients = {"primary_rpc": primary, "validation_rpc": validation}
    other_role = {"primary_rpc": "validation_rpc", "validation_rpc": "primary_rpc"}
    minimum_width = int(retrieval["minimum_viable_bulk_blocks_per_query"])
    attempted: list[str] = []

    for requested_width in (int(value) for value in retrieval["bulk_log_probe_widths"]):
        probe_end = min(maximum_block, probe_start_block + requested_width - 1)
        actual_width = probe_end - probe_start_block + 1
        if actual_width < minimum_width:
            continue
        probe_chunk = BlockChunk(probe_start_block, probe_end)
        parameters = {
            "address": pool_address,
            "topics": [topics],
            "fromBlock": hex(probe_start_block),
            "toBlock": hex(probe_end),
        }
        for role in (str(value) for value in retrieval["bulk_log_provider_order"]):
            client = clients[role]
            try:
                logs = canonicalize_logs(client.logs(parameters))
                validate_chunk_logs(
                    logs,
                    chunk=probe_chunk,
                    pool_address=pool_address,
                    topics=set(topics),
                )
            except RpcError as error:
                status = "range_limited" if _can_split_log_query(error) else "unavailable"
                attempted.append(f"{role}:{actual_width}:{status}")
                print(
                    f"bulk log probe: role={role}; blocks={actual_width}; status={status}",
                    flush=True,
                )
                continue

            crosscheck_role = other_role[role]
            print(
                f"bulk log probe: role={role}; blocks={actual_width}; "
                f"status=selected; logs={len(logs)}",
                flush=True,
            )
            return _BulkLogSelection(
                role=role,
                source_id=str(chain[role]["source_id"]),
                client=client,
                crosscheck_role=crosscheck_role,
                crosscheck_source_id=str(chain[crosscheck_role]["source_id"]),
                crosscheck_client=clients[crosscheck_role],
                query_width=actual_width,
            )

    attempted_text = ", ".join(attempted) if attempted else "no viable probe range"
    raise RuntimeError(
        "No configured Arbitrum RPC supports the minimum viable bulk eth_getLogs range "
        f"of {minimum_width} blocks ({attempted_text}). A Graph gateway key or a "
        "higher-range RPC is required; the extractor will not issue millions of tiny queries."
    )


def _complete_chunk_cache(
    chunks: list[BlockChunk], chunk_directory: str | Path
) -> bool:
    """Return true only when every expected atomic log chunk is present.

    This is intentionally an existence preflight, not a trust decision.  The shared
    ``fetch_log_chunks`` path still decompresses and validates every cached record before
    any provider comparison or release gate can run.
    """
    directory = Path(chunk_directory)
    return bool(chunks) and all((directory / chunk.name).is_file() for chunk in chunks)


def _cached_bulk_log_selection(
    *,
    chain: dict[str, Any],
    retrieval: dict[str, Any],
    cache_client: RpcClient,
    validation_client: RpcClient,
) -> _BulkLogSelection:
    """Describe a complete official-RPC cache while using the verifier for samples."""
    verification_variable = str(
        chain["validation_rpc"].get("environment_variable", "")
    )
    verification_source_id = (
        "arbitrum_user_configured_verifier"
        if verification_variable and os.getenv(verification_variable, "").strip()
        else str(chain["validation_rpc"]["source_id"])
    )
    return _BulkLogSelection(
        role="validated_cache",
        source_id=str(chain["validation_rpc"]["source_id"]),
        client=cache_client,
        crosscheck_role="validation_rpc",
        crosscheck_source_id=verification_source_id,
        crosscheck_client=validation_client,
        query_width=int(retrieval["initial_blocks_per_log_query"]),
    )


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _single_provider_boundary_checks(
    boundaries: list[dict[str, Any]], client: RpcClient
) -> list[dict[str, Any]]:
    week_zero = next(
        index for index, row in enumerate(boundaries) if int(row["boundary_event_week"]) == 0
    )
    indices = sorted({0, week_zero, len(boundaries) - 2, len(boundaries) - 1})
    rows: list[dict[str, Any]] = []
    for index in indices:
        expected = boundaries[index]
        number = int(expected["start_block"])
        observed = client.block(number)
        expected_hash = str(expected["start_block_hash"]).lower()
        expected_timestamp = int(parse_utc(expected["start_block_timestamp"]).timestamp())
        rows.append(
            {
                "boundary_event_week": int(expected["boundary_event_week"]),
                "block_number": number,
                "expected_block_hash": expected_hash,
                "primary_block_hash": str(observed["hash"]).lower(),
                "validation_block_hash": "",
                "expected_timestamp_utc": expected["start_block_timestamp"],
                "exact_match": (
                    str(observed["hash"]).lower() == expected_hash
                    and int_from_hex(str(observed["timestamp"])) == expected_timestamp
                ),
                "verification_scope": "same_provider_replay_only",
            }
        )
    if not all(row["exact_match"] for row in rows):
        raise ValueError("Primary-provider boundary replay failed")
    return rows


def _historical_contract_code_check(
    primary: RpcClient,
    validation: RpcClient,
    *,
    address: str,
    block_number: int,
    endpoints_independent: bool,
) -> tuple[str, str, bool, str, dict[str, str] | None]:
    """Compare historical bytecode while failing closed on a non-archive verifier."""
    primary_code = primary.code(address, block_number)
    if primary_code == "0x":
        raise ValueError("Aave Arbitrum Pool bytecode is absent at the activation block")

    try:
        validation_code = validation.code(address, block_number)
    except RpcError as error:
        return primary_code, "", False, "validation_historical_state_unavailable", {
            "error_type": type(error).__name__,
            "redacted_message": str(error),
        }

    if validation_code == "0x":
        return (
            primary_code,
            validation_code,
            False,
            "validation_historical_code_absent",
            None,
        )

    exact_match = primary_code == validation_code
    if endpoints_independent and not exact_match:
        raise ValueError("Aave Arbitrum Pool bytecode differs across independent providers")
    return (
        primary_code,
        validation_code,
        exact_match,
        "independent_exact_match" if endpoints_independent else "same_endpoint_replay",
        None,
    )


def _consensus_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only fields whose equality establishes the same historical EVM log."""
    records: list[dict[str, Any]] = []
    for index, log in enumerate(canonicalize_logs(logs)):
        missing = [field for field in CONSENSUS_LOG_FIELDS if field not in log]
        if missing:
            raise ValueError(
                f"eth_getLogs record {index} is missing consensus fields: "
                + ", ".join(missing)
            )
        records.append({field: log[field] for field in CONSENSUS_LOG_FIELDS})
    return records


def _log_identity(log: dict[str, Any]) -> tuple[str, int]:
    return (
        str(log["transactionHash"]).lower(),
        int_from_hex(str(log["logIndex"])),
    )


def _index_logs(logs: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for log in canonicalize_logs(logs):
        identity = _log_identity(log)
        if identity in indexed:
            raise ValueError(
                "Duplicate eth_getLogs identity in provider sample: "
                f"{identity[0]}:{identity[1]}"
            )
        indexed[identity] = log
    return indexed


def _field_level_log_differences(
    primary_logs: list[dict[str, Any]],
    validation_logs: list[dict[str, Any]],
    *,
    from_block: int,
    to_block: int,
) -> list[dict[str, Any]]:
    """Produce value-free, hash-backed diagnostics for every observed response field."""
    primary = _index_logs(primary_logs)
    validation = _index_logs(validation_logs)
    primary_ids = set(primary)
    validation_ids = set(validation)
    matched_ids = primary_ids.intersection(validation_ids)
    primary_only = primary_ids.difference(validation_ids)
    validation_only = validation_ids.difference(primary_ids)
    fields = sorted(
        set(CONSENSUS_LOG_FIELDS)
        | {field for log in primary.values() for field in log}
        | {field for log in validation.values() for field in log}
    )

    diagnostics: list[dict[str, Any]] = []
    for field in fields:
        primary_values = [
            [identity[0], identity[1], primary[identity][field]]
            for identity in sorted(primary)
            if field in primary[identity]
        ]
        validation_values = [
            [identity[0], identity[1], validation[identity][field]]
            for identity in sorted(validation)
            if field in validation[identity]
        ]
        primary_missing = sum(field not in primary[identity] for identity in matched_ids)
        validation_missing = sum(
            field not in validation[identity] for identity in matched_ids
        )
        value_mismatches = sum(
            field in primary[identity]
            and field in validation[identity]
            and primary[identity][field] != validation[identity][field]
            for identity in matched_ids
        )
        exact_match = (
            not primary_only
            and not validation_only
            and not primary_missing
            and not validation_missing
            and not value_mismatches
            and len(primary_values) == len(validation_values)
        )
        diagnostics.append(
            {
                "from_block": from_block,
                "to_block": to_block,
                "field": field,
                "field_class": (
                    "consensus"
                    if field in CONSENSUS_LOG_FIELDS
                    else "provider_metadata"
                ),
                "primary_log_count": len(primary),
                "validation_log_count": len(validation),
                "matched_identity_count": len(matched_ids),
                "primary_only_identity_count": len(primary_only),
                "validation_only_identity_count": len(validation_only),
                "primary_present_count": len(primary_values),
                "validation_present_count": len(validation_values),
                "primary_missing_on_matched_count": primary_missing,
                "validation_missing_on_matched_count": validation_missing,
                "value_mismatch_count": value_mismatches,
                "primary_field_sha256": canonical_json_sha256(primary_values),
                "validation_field_sha256": canonical_json_sha256(validation_values),
                "exact_match": exact_match,
            }
        )
    return diagnostics


def cross_provider_consensus_checks(
    bulk_logs: list[dict[str, Any]],
    validation_client: RpcClient,
    *,
    pool_address: str,
    topics: list[str],
    sample_count: int,
    sample_width: int,
    minimum_block: int,
    maximum_block: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare four samples on consensus fields and audit provider-only metadata."""
    checks: list[dict[str, Any]] = []
    field_differences: list[dict[str, Any]] = []
    for sample in validation_windows(
        bulk_logs,
        count=sample_count,
        width=sample_width,
        minimum_block=minimum_block,
        maximum_block=maximum_block,
    ):
        primary = canonicalize_logs(
            [
                log
                for log in bulk_logs
                if sample.from_block
                <= int_from_hex(str(log["blockNumber"]))
                <= sample.to_block
            ]
        )
        validation = canonicalize_logs(
            validation_client.logs(
                {
                    "address": pool_address,
                    "topics": [topics],
                    "fromBlock": hex(sample.from_block),
                    "toBlock": hex(sample.to_block),
                }
            )
        )
        primary_consensus = _consensus_logs(primary)
        validation_consensus = _consensus_logs(validation)
        primary_consensus_hash = canonical_json_sha256(primary_consensus)
        validation_consensus_hash = canonical_json_sha256(validation_consensus)
        primary_full_hash = canonical_json_sha256(primary)
        validation_full_hash = canonical_json_sha256(validation)
        consensus_match = primary_consensus_hash == validation_consensus_hash
        full_payload_match = primary_full_hash == validation_full_hash
        primary_metadata = sorted(
            {field for log in primary for field in log}.difference(CONSENSUS_LOG_FIELDS)
        )
        validation_metadata = sorted(
            {field for log in validation for field in log}.difference(
                CONSENSUS_LOG_FIELDS
            )
        )
        checks.append(
            {
                "from_block": sample.from_block,
                "to_block": sample.to_block,
                "comparison_schema": LOG_COMPARISON_SCHEMA,
                "primary_log_count": len(primary),
                "validation_log_count": len(validation),
                "primary_consensus_sha256": primary_consensus_hash,
                "validation_consensus_sha256": validation_consensus_hash,
                "primary_full_payload_sha256": primary_full_hash,
                "validation_full_payload_sha256": validation_full_hash,
                "primary_metadata_fields": ";".join(primary_metadata),
                "validation_metadata_fields": ";".join(validation_metadata),
                "full_payload_exact_match": full_payload_match,
                "provider_metadata_difference_only": (
                    consensus_match and not full_payload_match
                ),
                "exact_match": consensus_match,
                "validation_status": (
                    "full_payload_exact_match"
                    if full_payload_match
                    else "consensus_match_provider_metadata_differs"
                    if consensus_match
                    else "consensus_mismatch"
                ),
            }
        )
        field_differences.extend(
            _field_level_log_differences(
                primary,
                validation,
                from_block=sample.from_block,
                to_block=sample.to_block,
            )
        )
    if not checks:
        raise ValueError("Independent-provider log validation produced no samples")
    return checks, field_differences


def beneficiary_metrics(addresses: list[str]) -> dict[str, float | int]:
    counts = Counter(address.lower() for address in addresses if address)
    observations = sum(counts.values())
    if observations == 0:
        return {
            "event_count": 0,
            "active_beneficiary_addresses": 0,
            "beneficiary_hhi": 0.0,
            "beneficiary_entropy": 0.0,
            "normalized_beneficiary_entropy": 0.0,
            "effective_beneficiary_addresses": 0.0,
            "inverse_hhi_beneficiary_addresses": 0.0,
            "top1_beneficiary_share": 0.0,
            "top10_beneficiary_share": 0.0,
            "event_split_actor_hhi_lower": 0.0,
            "stable_address_actor_hhi_lower": 0.0,
            "actor_hhi_upper": 0.0,
        }
    ordered = sorted(counts.values(), reverse=True)
    shares = [count / observations for count in ordered]
    hhi = sum(share * share for share in shares)
    entropy = -sum(share * math.log(share) for share in shares)
    address_count = len(shares)
    normalized = entropy / math.log(address_count) if address_count > 1 else 0.0
    return {
        "event_count": observations,
        "active_beneficiary_addresses": address_count,
        "beneficiary_hhi": hhi,
        "beneficiary_entropy": entropy,
        "normalized_beneficiary_entropy": normalized,
        "effective_beneficiary_addresses": math.exp(entropy),
        "inverse_hhi_beneficiary_addresses": 1.0 / hhi,
        "top1_beneficiary_share": shares[0],
        "top10_beneficiary_share": sum(shares[:10]),
        "event_split_actor_hhi_lower": 1.0 / observations,
        "stable_address_actor_hhi_lower": hhi,
        "actor_hhi_upper": 1.0,
    }


def build_weekly_beneficiary_panel(
    records: pd.DataFrame,
    calendar: list[dict[str, Any]],
    *,
    chain: str,
    chain_id: int,
    cohort_id: str,
) -> list[dict[str, Any]]:
    required = {"event_week", "beneficiary_address", "action"}
    missing = sorted(required.difference(records.columns))
    if missing:
        raise ValueError(f"real_v5 beneficiary input is missing columns: {missing}")
    rows: list[dict[str, Any]] = []
    for period in calendar:
        event_week = int(period["event_week"])
        group = records.loc[records["event_week"].astype(int) == event_week]
        metrics = beneficiary_metrics(group["beneficiary_address"].astype(str).tolist())
        rows.append(
            {
                "cohort_id": cohort_id,
                "chain": chain,
                "chain_id": chain_id,
                "event_week": event_week,
                "window_start_utc": period["window_start_utc"],
                "window_end_utc_exclusive": period["window_end_utc_exclusive"],
                "action_count": int(group["action"].nunique()),
                **metrics,
                "observed_unit": "beneficiary_address",
                "observed_unit_label": "position-holder address",
                "measurement_status": "address_proxy_with_actor_bounds",
                "causal_status": "descriptive_input_only",
            }
        )
    return rows


def _comparable_action_rows(
    ethereum_path: Path, arbitrum_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ethereum = pd.read_csv(ethereum_path)
    ethereum.insert(1, "chain", "Ethereum")
    arbitrum = pd.DataFrame(arbitrum_rows)
    arbitrum.insert(1, "chain", "Arbitrum")
    combined = pd.concat([ethereum, arbitrum], ignore_index=True, sort=False)
    combined = combined.sort_values(["event_week", "chain_id", "action"], kind="stable")
    return combined.fillna("").to_dict(orient="records")


def build_comparable_beneficiary_panel(
    ethereum_panel_path: str | Path,
    arbitrum_records: pd.DataFrame,
    ethereum_calendar: list[dict[str, Any]],
    arbitrum_calendar: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ethereum = pd.read_csv(ethereum_panel_path)
    ethereum_rows = build_weekly_beneficiary_panel(
        ethereum,
        ethereum_calendar,
        chain="Ethereum",
        chain_id=1,
        cohort_id="ethereum_gho",
    )
    arbitrum_rows = build_weekly_beneficiary_panel(
        arbitrum_records,
        arbitrum_calendar,
        chain="Arbitrum",
        chain_id=42161,
        cohort_id="arbitrum_gho",
    )
    rows = ethereum_rows + arbitrum_rows
    rows.sort(key=lambda row: (int(row["event_week"]), int(row["chain_id"])))
    return rows


def _gho_weekly_rows(
    ethereum_reserve_panel: Path,
    arbitrum_reserve_rows: list[dict[str, Any]],
    *,
    ethereum_gho: str,
    arbitrum_gho: str,
) -> list[dict[str, Any]]:
    ethereum = pd.read_csv(ethereum_reserve_panel)
    ethereum = ethereum.loc[
        ethereum["reserve_address"].astype(str).str.lower() == ethereum_gho.lower()
    ].copy()
    ethereum.insert(1, "chain", "Ethereum")
    arbitrum = pd.DataFrame(arbitrum_reserve_rows)
    arbitrum = arbitrum.loc[
        arbitrum["reserve_address"].astype(str).str.lower() == arbitrum_gho.lower()
    ].copy()
    arbitrum.insert(1, "chain", "Arbitrum")
    combined = pd.concat([ethereum, arbitrum], ignore_index=True, sort=False)
    combined = combined.sort_values(["event_week", "chain_id", "action"], kind="stable")
    return combined.fillna("").to_dict(orient="records")


def run_real_v5_arbitrum_candidate(
    root: str | Path = ".",
    *,
    config_path: str | Path = "configs/real_v5_arbitrum.yaml",
    rpc_url: str | None = None,
    validation_rpc_url: str | None = None,
    resume: bool = True,
) -> dict[str, Path]:
    project = Path(root).resolve()
    configuration_path = project / config_path
    config = load_real_v5_config(configuration_path)
    chain = config["chain"]
    retrieval = config["retrieval"]
    window = config["window"]
    comparison = config["comparison"]

    primary_url = _validated_rpc_url(
        rpc_url or _required_rpc_url(chain["primary_rpc"]), label="primary RPC"
    )
    secondary_url = _validated_rpc_url(
        validation_rpc_url or _validation_rpc_url(chain["validation_rpc"]),
        label="validation RPC",
    )
    independent_endpoint = _independent_endpoint(primary_url, secondary_url)

    primary_stats = RpcStats()
    validation_stats = RpcStats()
    primary = _RedactingRpcClient(
        primary_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
        stats=primary_stats,
    )
    validation = _RedactingRpcClient(
        secondary_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
        stats=validation_stats,
    )
    expected_chain_id = int(chain["chain_id"])
    if primary.chain_id() != expected_chain_id:
        raise ValueError("Primary RPC chain ID mismatch for Arbitrum")
    if validation.chain_id() != expected_chain_id:
        raise ValueError("Validation RPC chain ID mismatch for Arbitrum")

    calendar_path = project / window["calendar"]
    calendar = read_cohort_calendar(
        calendar_path,
        cohort_id=str(config["cohort_id"]),
        minimum_event_week=int(window["minimum_event_week"]),
        maximum_event_week=int(window["maximum_event_week"]),
    )
    activation_block = int(calendar[0]["activation_block"])
    activation_utc = parse_utc(str(calendar[0]["activation_utc"]))
    output = project / retrieval["output_directory"]
    comparable_output = project / retrieval["comparable_output_directory"]
    output.mkdir(parents=True, exist_ok=True)
    comparable_output.mkdir(parents=True, exist_ok=True)

    boundary_path = output / "block_boundaries.csv"
    targets = boundary_targets(calendar)
    if resume and boundary_path.exists():
        boundaries = read_and_validate_boundary_cache(boundary_path, targets)
        boundary_mode = "validated_cache"
    else:
        boundaries = resolve_boundaries(
            primary,
            targets,
            activation_block=activation_block,
            activation_utc=activation_utc,
            workers=int(retrieval["boundary_workers"]),
            seed_seconds_per_block=int(retrieval["boundary_seed_seconds_per_block"]),
            initial_radius_blocks=int(retrieval["boundary_initial_radius_blocks"]),
        )
        write_csv_records(boundary_path, boundaries)
        boundary_mode = "rpc_exact_headers_with_activation_block_anchor"

    if independent_endpoint:
        boundary_checks = cross_provider_boundary_checks(boundaries, primary, validation)
        for row in boundary_checks:
            row["verification_scope"] = "independent_provider"
    else:
        boundary_checks = _single_provider_boundary_checks(boundaries, primary)
    boundary_check_path = output / "boundary_provider_checks.csv"
    write_csv_records(boundary_check_path, boundary_checks)

    pool_address = str(config["pool"]["address"]).lower()
    primary_code, validation_code, bytecode_match, code_validation_status, code_error = (
        _historical_contract_code_check(
            primary,
            validation,
            address=pool_address,
            block_number=activation_block,
            endpoints_independent=independent_endpoint,
        )
    )
    contract_check = {
        "address": pool_address,
        "block_number": activation_block,
        "primary_source_id": chain["primary_rpc"]["source_id"],
        "validation_source_id": (
            "arbitrum_user_configured_verifier"
            if str(chain["validation_rpc"].get("environment_variable", ""))
            and os.getenv(
                str(chain["validation_rpc"]["environment_variable"]), ""
            ).strip()
            else chain["validation_rpc"]["source_id"]
        ),
        "primary_code_sha256": canonical_json_sha256(primary_code),
        "validation_code_sha256": (
            canonical_json_sha256(validation_code) if validation_code else None
        ),
        "exact_match": bytecode_match,
        "validation_status": code_validation_status,
        "validation_error": code_error,
        "verification_scope": (
            "independent_provider"
            if independent_endpoint and bytecode_match
            else "independent_gate_pending"
            if independent_endpoint
            else "same_endpoint_not_independent"
        ),
    }
    contract_check_path = _write_json(output / "contract_code_check.json", contract_check)

    first_block = int(boundaries[0]["start_block"])
    last_block = int(boundaries[-1]["start_block"]) - 1
    chunks = build_chunks(
        first_block,
        last_block,
        int(retrieval["maximum_blocks_per_request"]),
    )
    chunk_directory = project / retrieval["raw_chunk_directory"]
    if resume and _complete_chunk_cache(chunks, chunk_directory):
        print(
            "complete atomic log cache detected; skipping bulk provider capability probe",
            flush=True,
        )
        bulk_logs = _cached_bulk_log_selection(
            chain=chain,
            retrieval=retrieval,
            cache_client=primary,
            validation_client=validation,
        )
    else:
        bulk_logs = _select_bulk_log_source(
            chain=chain,
            retrieval=retrieval,
            primary=primary,
            validation=validation,
            pool_address=pool_address,
            topics=event_topics(),
            probe_start_block=first_block,
            maximum_block=last_block,
        )
    bulk_runtime = _bulk_log_runtime(bulk_logs, retrieval)
    print(
        "bulk log runtime: "
        f"source_id={bulk_logs.source_id}; workers={bulk_runtime.workers}; "
        f"minimum_start_interval_seconds={bulk_runtime.minimum_interval_seconds}; "
        f"rate_limit_cooldown_seconds={bulk_runtime.rate_limit_cooldown_seconds}",
        flush=True,
    )
    raw_logs, chunk_records = fetch_log_chunks(
        bulk_runtime.client,
        chunks,
        pool_address=pool_address,
        topics=event_topics(),
        chunk_directory=chunk_directory,
        project_root=project,
        workers=bulk_runtime.workers,
        resume=resume,
        progress_every=int(retrieval["progress_every_chunks"]),
        progress_interval_seconds=float(retrieval["progress_interval_seconds"]),
        initial_query_width=bulk_logs.query_width,
        minimum_query_width=int(retrieval["minimum_adaptive_blocks_per_log_query"]),
        maximum_pending=bulk_runtime.maximum_pending,
        maximum_runtime_seconds=float(retrieval["maximum_runtime_seconds"]),
    )
    for row in chunk_records:
        row["retrieval_mode"] = bulk_logs.role
        row["source_id"] = bulk_logs.source_id
    chunk_manifest_path = write_csv_records(output / "retrieval_chunks.csv", chunk_records)
    raw_log_canonical_hash = _canonical_json_list_sha256(raw_logs)

    log_checks, log_field_differences = cross_provider_consensus_checks(
        raw_logs,
        bulk_logs.crosscheck_client,
        pool_address=pool_address,
        topics=event_topics(),
        sample_count=int(retrieval["validation_sample_count"]),
        sample_width=int(retrieval["validation_sample_width_blocks"]),
        minimum_block=first_block,
        maximum_block=last_block,
    )
    for row in log_checks:
        row["bulk_log_source_id"] = bulk_logs.source_id
        row["validation_source_id"] = bulk_logs.crosscheck_source_id
        row["verification_scope"] = (
            "independent_provider" if independent_endpoint else "same_endpoint_not_independent"
        )
    log_check_path = write_csv_records(output / "cross_provider_checks.csv", log_checks)
    for row in log_field_differences:
        row["bulk_log_source_id"] = bulk_logs.source_id
        row["validation_source_id"] = bulk_logs.crosscheck_source_id
    log_field_difference_path = write_csv_records(
        output / "cross_provider_field_differences.csv", log_field_differences
    )

    for index, log in enumerate(raw_logs):
        raw_logs[index] = decode_pool_log(
            log,
            chain_id=expected_chain_id,
            pool_address=pool_address,
        )
    decoded = assign_event_weeks(raw_logs, boundaries)
    del raw_logs
    processed_path = project / retrieval["processed_event_path"]
    _write_processed_events(processed_path, decoded)

    weekly_rows = build_weekly_action_panel(decoded, calendar)
    reserve_weekly_rows = build_reserve_week_action_panel(decoded, calendar)
    weekly_path = write_csv_records(output / "weekly_action_panel.csv", weekly_rows)
    reserve_weekly_path = write_csv_records(
        output / "reserve_week_action_panel.csv", reserve_weekly_rows
    )

    event_count = len(decoded)
    transaction_count = len({record["tx_hash"] for record in decoded})
    beneficiary_address_count = len(
        {str(record["beneficiary_address"]).lower() for record in decoded}
    )
    reserve_count = len(
        {str(record["reserve_address"]).lower() for record in decoded}
    )
    action_counts = Counter(str(record["action"]) for record in decoded)
    arbitrum_frame = pd.DataFrame(decoded)
    del decoded
    ethereum_calendar = read_cohort_calendar(
        calendar_path,
        cohort_id="ethereum_gho",
        minimum_event_week=int(window["minimum_event_week"]),
        maximum_event_week=int(window["maximum_event_week"]),
    )
    comparable_rows = build_comparable_beneficiary_panel(
        project / comparison["ethereum_beneficiary_panel"],
        arbitrum_frame,
        ethereum_calendar,
        calendar,
    )
    del arbitrum_frame
    comparable_path = write_csv_records(
        comparable_output / "weekly_beneficiary_panel.csv", comparable_rows
    )
    comparable_action_rows = _comparable_action_rows(
        project / comparison["ethereum_weekly_action_panel"], weekly_rows
    )
    comparable_action_path = write_csv_records(
        comparable_output / "weekly_action_panel.csv", comparable_action_rows
    )
    gho_rows = _gho_weekly_rows(
        project / comparison["ethereum_reserve_week_action_panel"],
        reserve_weekly_rows,
        ethereum_gho=str(comparison["ethereum_gho_address"]),
        arbitrum_gho=str(config["gho"]["underlying_address"]),
    )
    if not gho_rows:
        raise ValueError("No Ethereum or Arbitrum GHO action rows were produced")
    gho_path = write_csv_records(comparable_output / "weekly_gho_action_panel.csv", gho_rows)

    provider_gate_passed = (
        independent_endpoint
        and bytecode_match
        and all(bool(row["exact_match"]) for row in boundary_checks)
        and all(bool(row["exact_match"]) for row in log_checks)
    )
    summary = {
        "schema_version": 1,
        "release_version": config["release_version"],
        "status": (
            "audited_descriptive_candidate"
            if provider_gate_passed
            else "candidate_independent_gate_pending"
        ),
        "cohort_id": config["cohort_id"],
        "chain_id": expected_chain_id,
        "pool_address": pool_address,
        "gho_address": str(config["gho"]["underlying_address"]).lower(),
        "window": {
            "minimum_event_week": int(window["minimum_event_week"]),
            "maximum_event_week": int(window["maximum_event_week"]),
            "start_utc": boundaries[0]["target_utc"],
            "end_utc_exclusive": boundaries[-1]["target_utc"],
            "first_block": first_block,
            "last_block": last_block,
        },
        "event_count": event_count,
        "transaction_count": transaction_count,
        "beneficiary_address_count": beneficiary_address_count,
        "reserve_count": reserve_count,
        "action_counts": dict(sorted(action_counts.items())),
        "retrieval_chunk_count": len(chunk_records),
        "bulk_log_source_id": bulk_logs.source_id,
        "bulk_log_provider_role": bulk_logs.role,
        "bulk_log_query_width": bulk_logs.query_width,
        "bulk_log_workers": bulk_runtime.workers,
        "bulk_log_minimum_start_interval_seconds": (
            bulk_runtime.minimum_interval_seconds
        ),
        "bulk_log_rate_limit_cooldown_seconds": (
            bulk_runtime.rate_limit_cooldown_seconds
        ),
        "log_validation_source_id": bulk_logs.crosscheck_source_id,
        "log_comparison_schema": LOG_COMPARISON_SCHEMA,
        "independent_provider_endpoint": independent_endpoint,
        "independent_boundary_samples_match": independent_endpoint
        and all(bool(row["exact_match"]) for row in boundary_checks),
        "independent_log_samples_match": independent_endpoint
        and all(bool(row["exact_match"]) for row in log_checks),
        "independent_log_full_payload_samples_match": independent_endpoint
        and all(bool(row["full_payload_exact_match"]) for row in log_checks),
        "provider_metadata_difference_only_sample_count": sum(
            bool(row["provider_metadata_difference_only"]) for row in log_checks
        ),
        "independent_contract_code_matches": independent_endpoint and bytecode_match,
        "independent_provider_gate_passed": provider_gate_passed,
        "comparable_weekly_rows": len(comparable_rows),
        "comparable_weekly_action_rows": len(comparable_action_rows),
        "gho_weekly_action_rows": len(gho_rows),
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
        "limitations": [
            "Beneficiary addresses are not verified natural persons or economic actors.",
            "Token-native amounts are never aggregated across different reserve addresses.",
            "The two chains have different activation dates and market conditions.",
            "The capability-selected public bulk RPC has no uptime or rate-limit guarantee.",
            "Provider-added log metadata is excluded from consensus hashes but retained "
            "in field-level diagnostics.",
            "No causal coefficient is produced by this extraction and comparison layer.",
        ],
    }
    summary_path = _write_json(output / "summary.json", summary)

    tracked = [
        boundary_path,
        boundary_check_path,
        contract_check_path,
        chunk_manifest_path,
        weekly_path,
        reserve_weekly_path,
        log_check_path,
        log_field_difference_path,
        comparable_path,
        comparable_action_path,
        gho_path,
        summary_path,
    ]
    manifest = {
        "schema_version": 1,
        "pipeline": "real_v5_arbitrum_aave_v3_pool_actions",
        "release_version": config["release_version"],
        "source_revision": source_revision(project),
        "configuration": {
            "path": project_relative_path(configuration_path, project),
            "sha256": sha256_file(configuration_path),
        },
        "calendar": {
            "path": project_relative_path(calendar_path, project),
            "sha256": sha256_file(calendar_path),
        },
        "ethereum_beneficiary_input": {
            "path": str(comparison["ethereum_beneficiary_panel"]),
            "sha256": sha256_file(project / comparison["ethereum_beneficiary_panel"]),
        },
        "providers": {
            "primary_source_id": chain["primary_rpc"]["source_id"],
            "primary_endpoint": safe_rpc_endpoint(primary_url),
            "validation_source_id": chain["validation_rpc"]["source_id"],
            "validation_endpoint": safe_rpc_endpoint(secondary_url),
            "endpoints_independent": independent_endpoint,
            "bulk_log_source_id": bulk_logs.source_id,
            "bulk_log_provider_role": bulk_logs.role,
            "bulk_log_query_width": bulk_logs.query_width,
            "bulk_log_workers": bulk_runtime.workers,
            "bulk_log_minimum_start_interval_seconds": (
                bulk_runtime.minimum_interval_seconds
            ),
            "bulk_log_rate_limit_cooldown_seconds": (
                bulk_runtime.rate_limit_cooldown_seconds
            ),
            "log_validation_source_id": bulk_logs.crosscheck_source_id,
            "log_validation_provider_role": bulk_logs.crosscheck_role,
            "log_comparison_schema": LOG_COMPARISON_SCHEMA,
            "log_consensus_fields": list(CONSENSUS_LOG_FIELDS),
        },
        "rpc_statistics": {
            "primary": primary_stats.to_dict(),
            "validation": validation_stats.to_dict(),
        },
        "boundary_retrieval_mode": boundary_mode,
        "raw_log_canonical_sha256": raw_log_canonical_hash,
        "processed_event_canonical_csv_sha256": gzip_payload_sha256(processed_path),
        "raw_chunks": chunk_records,
        "artifacts": {
            project_relative_path(path, project): sha256_file(path) for path in tracked
        },
        "source_code": {
            relative: sha256_file(project / relative)
            for relative in [
                "src/aave_bns/evm_rpc.py",
                "src/aave_bns/aave_v3_events.py",
                "src/aave_bns/real_v2_ethereum.py",
                "src/aave_bns/real_v5_arbitrum.py",
                "scripts/run_real_v5_arbitrum.py",
            ]
        },
        "independent_provider_gate_passed": summary["independent_provider_gate_passed"],
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }
    manifest_path = output / "manifest.json"
    write_manifest(manifest_path, manifest)
    return {
        "boundaries": boundary_path,
        "boundary_checks": boundary_check_path,
        "contract_check": contract_check_path,
        "chunks": chunk_manifest_path,
        "processed_events": processed_path,
        "weekly_panel": weekly_path,
        "reserve_week_panel": reserve_weekly_path,
        "cross_provider_checks": log_check_path,
        "cross_provider_field_differences": log_field_difference_path,
        "comparable_weekly_panel": comparable_path,
        "comparable_weekly_action_panel": comparable_action_path,
        "gho_weekly_panel": gho_path,
        "summary": summary_path,
        "manifest": manifest_path,
    }
