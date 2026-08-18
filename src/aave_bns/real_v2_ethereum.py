from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from datetime import datetime, timezone
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
    resolve_first_block_at_or_after,
)
from .provenance import sha256_file, utc_now_iso, write_manifest
from .real_v2 import parse_utc


@dataclass(frozen=True)
class BlockChunk:
    from_block: int
    to_block: int

    @property
    def name(self) -> str:
        return f"{self.from_block:09d}_{self.to_block:09d}.jsonl.gz"


def load_ethereum_config(path: str | Path = "configs/real_v2_ethereum.yaml") -> dict[str, Any]:
    config = load_yaml(path)
    if int(config.get("schema_version", 0)) != 1:
        raise ValueError("Ethereum real_v2 extraction requires schema_version 1")
    if int(config["chain"]["chain_id"]) != 1:
        raise ValueError("The Ethereum extractor requires chain_id 1")
    configured = {
        str(row["action"]): (str(row["signature"]), str(row["topic0"]).lower())
        for row in config["events"]
    }
    expected = {spec.action: (spec.signature, spec.topic0) for spec in EVENT_SPECS}
    if configured != expected:
        raise ValueError("Configured Aave event signatures or topics do not match the decoder")
    retrieval = config["retrieval"]
    if int(retrieval["maximum_blocks_per_request"]) > 10_000:
        raise ValueError("The registered primary provider allows at most 10,000 blocks per request")
    return config


def resolve_rpc_url(configured: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override
    environment_variable = str(configured["environment_variable"])
    return os.getenv(environment_variable, str(configured["url"]))


def safe_rpc_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.hostname:
        return "redacted-invalid-endpoint"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def project_relative_path(path: str | Path, project_root: str | Path) -> str:
    """Return a portable POSIX path and reject artifacts outside the project root."""
    project = Path(project_root).resolve()
    candidate = Path(path).resolve()
    try:
        return candidate.relative_to(project).as_posix()
    except ValueError as error:
        raise ValueError(f"Artifact path is outside the project root: {candidate}") from error


def source_revision(project_root: str | Path) -> str:
    override = os.getenv("AAVE_BNS_SOURCE_REVISION")
    if override:
        value = override.strip().lower()
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("AAVE_BNS_SOURCE_REVISION must be a full 40-character Git SHA")
        return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(project_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    value = result.stdout.strip().lower()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        return "unavailable"
    return value


def read_cohort_calendar(
    path: str | Path,
    *,
    cohort_id: str,
    minimum_event_week: int,
    maximum_event_week: int,
) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        selected = [row for row in csv.DictReader(handle) if row["cohort_id"] == cohort_id]
    selected.sort(key=lambda row: int(row["event_week"]))
    expected_weeks = list(range(minimum_event_week, maximum_event_week + 1))
    observed_weeks = [int(row["event_week"]) for row in selected]
    if observed_weeks != expected_weeks:
        raise ValueError(
            f"Calendar weeks for {cohort_id} are {observed_weeks}; expected {expected_weeks}"
        )
    return selected


def boundary_targets(calendar: list[dict[str, Any]]) -> list[tuple[int, datetime]]:
    targets = [
        (int(row["event_week"]), parse_utc(str(row["window_start_utc"]))) for row in calendar
    ]
    final_week = int(calendar[-1]["event_week"]) + 1
    targets.append((final_week, parse_utc(str(calendar[-1]["window_end_utc_exclusive"]))))
    return targets


def resolve_boundaries(
    client: RpcClient,
    targets: list[tuple[int, datetime]],
    *,
    activation_block: int,
    activation_utc: datetime,
    workers: int,
    seed_seconds_per_block: int = 12,
    initial_radius_blocks: int = 20_000,
) -> list[dict[str, Any]]:
    if seed_seconds_per_block < 1 or initial_radius_blocks < 1:
        raise ValueError("Boundary search seed and radius must be positive")
    latest = client.latest_block_number()
    if int_from_hex(client.block(activation_block)["timestamp"]) != int(activation_utc.timestamp()):
        raise ValueError("Configured activation block timestamp does not match the chain")

    block_cache: dict[int, dict[str, Any]] = {}
    cache_lock = threading.Lock()

    class CachedClient:
        def block(self, block_number: int) -> dict[str, Any]:
            with cache_lock:
                cached = block_cache.get(block_number)
            if cached is not None:
                return cached
            observed = client.block(block_number)
            with cache_lock:
                return block_cache.setdefault(block_number, observed)

    cached_client = CachedClient()

    def observed_timestamp(block_number: int) -> int:
        return int_from_hex(cached_client.block(block_number)["timestamp"])

    def bracket(target: datetime) -> tuple[int, int]:
        offset_seconds = int(target.timestamp()) - int(activation_utc.timestamp())
        # This conversion is only a search seed. The bracket is expanded until observed
        # headers surround the target, and the final adjacent headers are proved exactly.
        estimated_block = activation_block + round(offset_seconds / seed_seconds_per_block)
        radius = initial_radius_blocks
        low = max(0, estimated_block - radius)
        high = min(latest, estimated_block + radius)
        target_timestamp = int(target.timestamp())
        while observed_timestamp(low) >= target_timestamp:
            if low == 0:
                raise ValueError("Could not bracket target below block zero")
            high = low
            radius *= 2
            low = max(0, low - radius)
        while observed_timestamp(high) < target_timestamp:
            if high == latest:
                raise ValueError("Could not bracket target below the latest block")
            low = high
            radius *= 2
            high = min(latest, high + radius)
        return low, high

    def anchored_activation_boundary(target: datetime) -> dict[str, Any]:
        """Use the verified execution block when several L2 blocks share its second."""
        first = cached_client.block(activation_block)
        previous = cached_client.block(activation_block - 1) if activation_block > 0 else None
        first_timestamp = int_from_hex(first["timestamp"])
        previous_timestamp = int_from_hex(previous["timestamp"]) if previous else None
        target_timestamp = int(target.astimezone(timezone.utc).timestamp())
        if first_timestamp != target_timestamp:
            raise ValueError("Configured activation block timestamp does not match the target")
        if previous_timestamp is not None and previous_timestamp > first_timestamp:
            raise ValueError("Activation-block timestamps are not monotone")
        return {
            "target_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "start_block": activation_block,
            "start_block_timestamp": datetime.fromtimestamp(
                first_timestamp, tz=timezone.utc
            )
            .isoformat()
            .replace("+00:00", "Z"),
            "start_block_hash": str(first["hash"]).lower(),
            "previous_block_timestamp": (
                datetime.fromtimestamp(previous_timestamp, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if previous_timestamp is not None
                else ""
            ),
            "lag_seconds": 0,
        }

    def resolve(item: tuple[int, datetime]) -> dict[str, Any]:
        event_week, target = item
        if target == activation_utc:
            # Arbitrum can produce multiple blocks with the same integer-second timestamp.
            # The policy boundary is the verified execution block, not the first block that
            # happens to share its timestamp.
            result = anchored_activation_boundary(target)
        else:
            low, high = bracket(target)
            result = resolve_first_block_at_or_after(
                cached_client,  # type: ignore[arg-type]
                target,
                low_block=low,
                high_block=high,
            )
        result["boundary_event_week"] = event_week
        return result

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(resolve, item): item for item in targets}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            print(
                f"resolved {index}/{len(targets)} boundaries; "
                f"week={result['boundary_event_week']}; block={result['start_block']}",
                flush=True,
            )
    results.sort(key=lambda row: int(row["boundary_event_week"]))
    if [row["target_utc"] for row in results] != [
        target.isoformat().replace("+00:00", "Z") for _, target in targets
    ]:
        raise AssertionError("Resolved boundaries do not preserve calendar order")
    blocks = [int(row["start_block"]) for row in results]
    if blocks != sorted(set(blocks)):
        raise ValueError("Boundary blocks must be unique and strictly increasing")
    return results


def write_csv_records(path: str | Path, records: list[dict[str, Any]]) -> Path:
    if not records:
        raise ValueError("Cannot write an empty record set")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return destination


def read_and_validate_boundary_cache(
    path: str | Path,
    targets: list[tuple[int, datetime]],
) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    if len(records) != len(targets):
        raise ValueError("Cached boundary table has an unexpected row count")
    expected = [
        (
            event_week,
            target.isoformat().replace("+00:00", "Z"),
        )
        for event_week, target in targets
    ]
    observed = [(int(row["boundary_event_week"]), row["target_utc"]) for row in records]
    if observed != expected:
        raise ValueError("Cached boundary table does not match the locked calendar")
    blocks = [int(row["start_block"]) for row in records]
    if blocks != sorted(set(blocks)):
        raise ValueError("Cached boundary blocks are not strictly increasing")
    for row in records:
        target = parse_utc(row["target_utc"])
        selected = parse_utc(row["start_block_timestamp"])
        previous = parse_utc(row["previous_block_timestamp"])
        event_week = int(row["boundary_event_week"])
        if selected < target:
            raise ValueError("Cached boundary starts before its target")
        if event_week == 0:
            if selected != target or previous > target:
                raise ValueError("Cached treatment boundary is not the exact activation block")
        elif previous >= target:
            raise ValueError("Cached boundary does not prove the adjacent-header rule")
        if int(row["lag_seconds"]) != int((selected - target).total_seconds()):
            raise ValueError("Cached boundary lag is inconsistent")
        block_hash = row["start_block_hash"]
        if not block_hash.startswith("0x") or len(block_hash) != 66:
            raise ValueError("Cached boundary has a malformed block hash")
    return records


def cross_provider_boundary_checks(
    boundaries: list[dict[str, Any]],
    primary_client: RpcClient,
    validation_client: RpcClient,
) -> list[dict[str, Any]]:
    week_zero = next(
        index for index, row in enumerate(boundaries) if int(row["boundary_event_week"]) == 0
    )
    sample_indices = sorted({0, week_zero, len(boundaries) - 2, len(boundaries) - 1})

    def check(index: int) -> dict[str, Any]:
        expected = boundaries[index]
        block_number = int(expected["start_block"])
        primary = primary_client.block(block_number)
        validation = validation_client.block(block_number)
        expected_hash = str(expected["start_block_hash"]).lower()
        expected_timestamp = int(parse_utc(expected["start_block_timestamp"]).timestamp())
        matches = (
            str(primary["hash"]).lower() == expected_hash
            and str(validation["hash"]).lower() == expected_hash
            and int_from_hex(primary["timestamp"]) == expected_timestamp
            and int_from_hex(validation["timestamp"]) == expected_timestamp
        )
        return {
            "boundary_event_week": int(expected["boundary_event_week"]),
            "block_number": block_number,
            "expected_block_hash": expected_hash,
            "primary_block_hash": str(primary["hash"]).lower(),
            "validation_block_hash": str(validation["hash"]).lower(),
            "expected_timestamp_utc": expected["start_block_timestamp"],
            "exact_match": matches,
        }

    with ThreadPoolExecutor(max_workers=len(sample_indices)) as executor:
        records = list(executor.map(check, sample_indices))
    if not all(row["exact_match"] for row in records):
        raise ValueError("Cross-provider boundary validation failed")
    return records


def build_chunks(first_block: int, last_block: int, width: int) -> list[BlockChunk]:
    if first_block < 0 or last_block < first_block or width < 1:
        raise ValueError("Invalid block chunk specification")
    chunks = []
    start = first_block
    while start <= last_block:
        end = min(start + width - 1, last_block)
        chunks.append(BlockChunk(start, end))
        start = end + 1
    return chunks


def _write_deterministic_jsonl_gzip(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as temporary_handle:
        temporary = Path(temporary_handle.name)
    try:
        with temporary.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename=path.name.removesuffix(".gz"),
                fileobj=raw_handle,
                mode="wb",
                compresslevel=9,
                mtime=0,
            ) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as text_handle:
                    for record in records:
                        text_handle.write(
                            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_jsonl_gzip(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, mode="rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def gzip_payload_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_chunk_logs(
    logs: list[dict[str, Any]],
    *,
    chunk: BlockChunk,
    pool_address: str,
    topics: set[str],
) -> None:
    keys: set[tuple[int, str, int]] = set()
    for log in logs:
        block_number = int_from_hex(str(log["blockNumber"]))
        if not chunk.from_block <= block_number <= chunk.to_block:
            raise ValueError(f"Log block {block_number} is outside {chunk}")
        if str(log["address"]).lower() != pool_address.lower():
            raise ValueError("RPC returned a log from an unexpected contract")
        if str(log["topics"][0]).lower() not in topics:
            raise ValueError("RPC returned an unrequested event topic")
        if log.get("removed") is True:
            raise ValueError("RPC returned a removed log in the finalized historical window")
        key = (
            block_number,
            str(log["transactionHash"]).lower(),
            int_from_hex(str(log["logIndex"])),
        )
        if key in keys:
            raise ValueError(f"Duplicate log key inside chunk: {key}")
        keys.add(key)


def _can_split_log_query(error: RpcError) -> bool:
    """Return whether an exact log query should be retried as smaller block ranges."""
    message = str(error).lower()
    range_markers = (
        "block range",
        "log response size",
        "query returned more than",
        "response size exceeded",
        "too many results",
        "result limit",
        "range is too wide",
        "please limit",
        "-32005",
    )
    if any(marker in message for marker in range_markers):
        return True
    transient_or_access_markers = (
        "rate limit",
        "too many requests",
        "compute units",
        "capacity",
        "quota",
        "credits",
        "billing",
        "unauthorized",
        "forbidden",
        "invalid api key",
    )
    if any(marker in message for marker in transient_or_access_markers):
        return False
    # Providers sometimes return an empty HTTP 400 for a result-set limit. Because the
    # same filter has already succeeded on adjacent chunks, bisecting preserves exact
    # coverage and is safer than silently reducing the observation window.
    return "http 400" in message or "400 client error" in message


def _fetch_logs_adaptively(
    client: RpcClient,
    *,
    pool_address: str,
    topics: list[str],
    from_block: int,
    to_block: int,
    initial_query_width: int | None = None,
    minimum_query_width: int = 1,
) -> list[dict[str, Any]]:
    if minimum_query_width < 1:
        raise ValueError("minimum_query_width must be positive")
    if initial_query_width is not None:
        if initial_query_width < 1:
            raise ValueError("initial_query_width must be positive")
        if initial_query_width < minimum_query_width:
            raise ValueError("initial_query_width cannot be smaller than minimum_query_width")
        if to_block - from_block + 1 > initial_query_width:
            logs: list[dict[str, Any]] = []
            for query in build_chunks(from_block, to_block, initial_query_width):
                logs.extend(
                    _fetch_logs_adaptively(
                        client,
                        pool_address=pool_address,
                        topics=topics,
                        from_block=query.from_block,
                        to_block=query.to_block,
                        minimum_query_width=minimum_query_width,
                    )
                )
            return logs

    filter_parameters = {
        "address": pool_address,
        "topics": [topics],
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
    }
    try:
        return client.logs(filter_parameters)
    except RpcError as error:
        query_width = to_block - from_block + 1
        if query_width <= minimum_query_width or not _can_split_log_query(error):
            raise
        midpoint = (from_block + to_block) // 2
        print(
            "provider limited eth_getLogs range "
            f"{from_block}-{to_block}; retrying exact halves",
            flush=True,
        )
        return _fetch_logs_adaptively(
            client,
            pool_address=pool_address,
            topics=topics,
            from_block=from_block,
            to_block=midpoint,
            minimum_query_width=minimum_query_width,
        ) + _fetch_logs_adaptively(
            client,
            pool_address=pool_address,
            topics=topics,
            from_block=midpoint + 1,
            to_block=to_block,
            minimum_query_width=minimum_query_width,
        )


def fetch_log_chunks(
    client: RpcClient,
    chunks: list[BlockChunk],
    *,
    pool_address: str,
    topics: list[str],
    chunk_directory: str | Path,
    project_root: str | Path,
    workers: int,
    resume: bool,
    progress_every: int = 10,
    progress_interval_seconds: float = 30.0,
    initial_query_width: int | None = None,
    minimum_query_width: int = 1,
    maximum_pending: int | None = None,
    maximum_runtime_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    directory = Path(chunk_directory)
    topic_set = set(topics)
    if workers < 1:
        raise ValueError("workers must be positive")
    if progress_every < 1 or progress_interval_seconds <= 0:
        raise ValueError("Progress intervals must be positive")
    if minimum_query_width < 1:
        raise ValueError("minimum_query_width must be positive")
    if initial_query_width is not None and initial_query_width < minimum_query_width:
        raise ValueError("initial_query_width cannot be smaller than minimum_query_width")
    pending_limit = maximum_pending if maximum_pending is not None else workers
    if pending_limit < workers:
        raise ValueError("maximum_pending cannot be smaller than workers")
    if maximum_runtime_seconds is not None and maximum_runtime_seconds <= 0:
        raise ValueError("maximum_runtime_seconds must be positive when provided")

    def fetch(chunk: BlockChunk) -> tuple[BlockChunk, list[dict[str, Any]], str]:
        path = directory / chunk.name
        if resume and path.exists():
            cached = canonicalize_logs(_read_jsonl_gzip(path))
            validate_chunk_logs(
                cached,
                chunk=chunk,
                pool_address=pool_address,
                topics=topic_set,
            )
            return chunk, cached, "cache"
        logs = canonicalize_logs(
            _fetch_logs_adaptively(
                client,
                pool_address=pool_address,
                topics=topics,
                from_block=chunk.from_block,
                to_block=chunk.to_block,
                initial_query_width=initial_query_width,
                minimum_query_width=minimum_query_width,
            )
        )
        validate_chunk_logs(
            logs,
            chunk=chunk,
            pool_address=pool_address,
            topics=topic_set,
        )
        _write_deterministic_jsonl_gzip(path, logs)
        return chunk, logs, "rpc"

    completed: dict[tuple[int, int], tuple[list[dict[str, Any]], str]] = {}
    cache_completed = 0
    rpc_completed = 0
    total = len(chunks)
    chunk_iterator = iter(chunks)
    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}

        def fill_pending() -> None:
            while len(futures) < pending_limit:
                try:
                    chunk = next(chunk_iterator)
                except StopIteration:
                    return
                futures[executor.submit(fetch, chunk)] = chunk

        fill_pending()
        next_heartbeat = time.monotonic() + progress_interval_seconds
        try:
            while futures:
                now = time.monotonic()
                if (
                    maximum_runtime_seconds is not None
                    and now - started_at >= maximum_runtime_seconds
                ):
                    raise RuntimeError(
                        "retrieval time slice expired; rerun to restore completed chunks"
                    )
                timeout = max(0.0, next_heartbeat - now)
                if maximum_runtime_seconds is not None:
                    timeout = min(
                        timeout,
                        max(0.0, maximum_runtime_seconds - (now - started_at)),
                    )
                done, _ = wait(
                    futures,
                    timeout=timeout,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    if (
                        maximum_runtime_seconds is not None
                        and time.monotonic() - started_at >= maximum_runtime_seconds
                    ):
                        raise RuntimeError(
                            "retrieval time slice expired; rerun to restore completed chunks"
                        )
                    print(
                        "retrieval heartbeat: "
                        f"completed={len(completed)}/{total}; "
                        f"pending={len(futures)}; cache={cache_completed}; rpc={rpc_completed}",
                        flush=True,
                    )
                    next_heartbeat = time.monotonic() + progress_interval_seconds
                    continue

                for future in done:
                    futures.pop(future)
                    chunk, logs, mode = future.result()
                    completed[(chunk.from_block, chunk.to_block)] = (logs, mode)
                    if mode == "cache":
                        cache_completed += 1
                    else:
                        rpc_completed += 1
                    index = len(completed)
                    if index % progress_every == 0 or index == total:
                        print(
                            f"completed {index}/{total} chunks; "
                            f"latest={chunk.from_block}-{chunk.to_block}; logs={len(logs)}; "
                            f"cache={cache_completed}; rpc={rpc_completed}",
                            flush=True,
                        )
                fill_pending()
                if time.monotonic() >= next_heartbeat:
                    print(
                        "retrieval heartbeat: "
                        f"completed={len(completed)}/{total}; "
                        f"pending={len(futures)}; cache={cache_completed}; rpc={rpc_completed}",
                        flush=True,
                    )
                    next_heartbeat = time.monotonic() + progress_interval_seconds
        except Exception:
            for future in futures:
                future.cancel()
            raise

    all_logs: list[dict[str, Any]] = []
    chunk_records: list[dict[str, Any]] = []
    for chunk in chunks:
        logs, mode = completed[(chunk.from_block, chunk.to_block)]
        all_logs.extend(logs)
        path = directory / chunk.name
        chunk_records.append(
            {
                "from_block": chunk.from_block,
                "to_block": chunk.to_block,
                "block_count": chunk.to_block - chunk.from_block + 1,
                "log_count": len(logs),
                "retrieval_mode": mode,
                "canonical_log_sha256": canonical_json_sha256(logs),
                "compressed_file_sha256": sha256_file(path),
                "compressed_bytes": path.stat().st_size,
                "path": project_relative_path(path, project_root),
            }
        )
    canonical = canonicalize_logs(all_logs)
    keys = [
        (
            int_from_hex(log["blockNumber"]),
            str(log["transactionHash"]).lower(),
            int_from_hex(log["logIndex"]),
        )
        for log in canonical
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate logs were found across retrieval chunks")
    return canonical, chunk_records


def assign_event_weeks(
    records: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    starts = [int(row["start_block"]) for row in boundaries]
    first_week = int(boundaries[0]["boundary_event_week"])
    final_marker = int(boundaries[-1]["boundary_event_week"])
    assigned = []
    for record in records:
        position = bisect_right(starts, int(record["block_number"])) - 1
        event_week = first_week + position
        if position < 0 or event_week >= final_marker:
            raise ValueError(f"Event block is outside the locked panel: {record['block_number']}")
        item = dict(record)
        item["event_week"] = event_week
        assigned.append(item)
    return assigned


def address_concentration(records: Iterable[dict[str, Any]]) -> dict[str, float | int]:
    counts: Counter[str] = Counter()
    event_count = 0
    for record in records:
        event_count += 1
        addresses = {
            str(record[field]).lower()
            for field in ("actor_address", "beneficiary_address", "counterparty_address")
            if record.get(field)
        }
        counts.update(addresses)
    total = sum(counts.values())
    if total == 0:
        return {
            "active_addresses": 0,
            "address_event_incidences": 0,
            "activity_hhi": 0.0,
            "adjusted_activity_hhi": 0.0,
            "effective_active_addresses": 0.0,
        }
    shares = [count / total for count in counts.values()]
    hhi = sum(share**2 for share in shares)
    n = len(counts)
    adjusted = 1.0 if n == 1 else (hhi - 1 / n) / (1 - 1 / n)
    if not 0 <= adjusted <= 1 + 1e-12:
        raise AssertionError("Adjusted HHI is outside [0, 1]")
    return {
        "active_addresses": n,
        "address_event_incidences": total,
        "activity_hhi": hhi,
        "adjusted_activity_hhi": max(0.0, min(1.0, adjusted)),
        "effective_active_addresses": 1 / hhi,
    }


def build_weekly_action_panel(
    records: list[dict[str, Any]],
    calendar: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((int(record["event_week"]), str(record["action"])), []).append(record)
    rows = []
    actions = [spec.action for spec in EVENT_SPECS]
    for period in calendar:
        event_week = int(period["event_week"])
        for action in actions:
            group = grouped.get((event_week, action), [])
            concentration = address_concentration(group)
            rows.append(
                {
                    "cohort_id": period["cohort_id"],
                    "chain_id": int(period["chain_id"]),
                    "event_week": event_week,
                    "window_start_utc": period["window_start_utc"],
                    "window_end_utc_exclusive": period["window_end_utc_exclusive"],
                    "action": action,
                    "event_count": len(group),
                    "transaction_count": len({row["tx_hash"] for row in group}),
                    "reserve_count": len({row["reserve_address"] for row in group}),
                    **concentration,
                    "measurement_level": "address",
                    "weight_definition": "unique_address_event_incidence",
                    "causal_status": "descriptive_input_only",
                }
            )
    return rows


def build_reserve_week_action_panel(
    records: list[dict[str, Any]],
    calendar: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    periods = {int(row["event_week"]): row for row in calendar}
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (
            int(record["event_week"]),
            str(record["action"]),
            str(record["reserve_address"]),
        )
        grouped.setdefault(key, []).append(record)
    rows = []
    for (event_week, action, reserve), group in sorted(grouped.items()):
        period = periods[event_week]
        concentration = address_concentration(group)
        rows.append(
            {
                "cohort_id": period["cohort_id"],
                "chain_id": int(period["chain_id"]),
                "event_week": event_week,
                "window_start_utc": period["window_start_utc"],
                "window_end_utc_exclusive": period["window_end_utc_exclusive"],
                "action": action,
                "reserve_address": reserve,
                "event_count": len(group),
                "transaction_count": len({row["tx_hash"] for row in group}),
                "total_amount_raw": str(sum(int(row["amount_raw"]) for row in group)),
                "total_secondary_amount_raw": str(
                    sum(
                        int(row["secondary_amount_raw"])
                        for row in group
                        if row["secondary_amount_raw"]
                    )
                ),
                **concentration,
                "amount_unit": "reserve_native_integer",
                "causal_status": "descriptive_input_only",
            }
        )
    return rows


def validation_windows(
    logs: list[dict[str, Any]],
    *,
    count: int,
    width: int,
    minimum_block: int,
    maximum_block: int,
) -> list[BlockChunk]:
    if not logs or count < 1 or width < 1:
        raise ValueError("Validation samples require logs, a positive count, and a positive width")
    blocks = sorted({int_from_hex(str(log["blockNumber"])) for log in logs})
    if count == 1:
        selected = [blocks[len(blocks) // 2]]
    else:
        selected = [
            blocks[round(index * (len(blocks) - 1) / (count - 1))] for index in range(count)
        ]
    windows = []
    for block in selected:
        start = max(minimum_block, block - width // 2)
        start = min(start, maximum_block - width + 1)
        end = min(maximum_block, start + width - 1)
        windows.append(BlockChunk(start, end))
    unique = {(window.from_block, window.to_block): window for window in windows}
    return [unique[key] for key in sorted(unique)]


def cross_provider_checks(
    primary_logs: list[dict[str, Any]],
    validation_client: RpcClient,
    *,
    pool_address: str,
    topics: list[str],
    sample_count: int,
    sample_width: int,
    minimum_block: int,
    maximum_block: int,
) -> list[dict[str, Any]]:
    records = []
    for sample in validation_windows(
        primary_logs,
        count=sample_count,
        width=sample_width,
        minimum_block=minimum_block,
        maximum_block=maximum_block,
    ):
        primary = [
            log
            for log in primary_logs
            if sample.from_block <= int_from_hex(str(log["blockNumber"])) <= sample.to_block
        ]
        secondary = canonicalize_logs(
            validation_client.logs(
                {
                    "address": pool_address,
                    "topics": [topics],
                    "fromBlock": hex(sample.from_block),
                    "toBlock": hex(sample.to_block),
                }
            )
        )
        primary_hash = canonical_json_sha256(primary)
        secondary_hash = canonical_json_sha256(secondary)
        records.append(
            {
                "from_block": sample.from_block,
                "to_block": sample.to_block,
                "primary_log_count": len(primary),
                "validation_log_count": len(secondary),
                "primary_canonical_sha256": primary_hash,
                "validation_canonical_sha256": secondary_hash,
                "exact_match": primary_hash == secondary_hash,
            }
        )
    if not records or not all(row["exact_match"] for row in records):
        raise ValueError("Independent-provider log validation failed")
    return records


def _write_processed_events(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as temporary_handle:
        temporary = Path(temporary_handle.name)
    try:
        with temporary.open("wb") as raw_handle:
            with gzip.GzipFile(
                filename=path.name.removesuffix(".gz"),
                fileobj=raw_handle,
                mode="wb",
                compresslevel=9,
                mtime=0,
            ) as zipped:
                with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text_handle:
                    frame.to_csv(text_handle, index=False, lineterminator="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_ethereum_action_panel(
    root: str | Path = ".",
    *,
    config_path: str | Path = "configs/real_v2_ethereum.yaml",
    rpc_url: str | None = None,
    validation_rpc_url: str | None = None,
    resume: bool = True,
    boundaries_only: bool = False,
) -> dict[str, Path]:
    project = Path(root).resolve()
    configuration_path = project / config_path
    config = load_ethereum_config(configuration_path)
    chain = config["chain"]
    retrieval = config["retrieval"]
    window = config["window"]
    cohort_id = str(config["cohort_id"])
    primary_url = resolve_rpc_url(chain["primary_rpc"], rpc_url)
    secondary_url = resolve_rpc_url(chain["validation_rpc"], validation_rpc_url)
    stats = RpcStats()
    validation_stats = RpcStats()
    contract_stats = RpcStats()
    primary_client = RpcClient(
        primary_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
        stats=stats,
    )
    validation_client = RpcClient(
        secondary_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
        stats=validation_stats,
    )
    contract_url = resolve_rpc_url(chain["contract_validation_rpc"])
    contract_client = RpcClient(
        contract_url,
        timeout_seconds=float(retrieval["timeout_seconds"]),
        maximum_attempts=int(retrieval["maximum_attempts"]),
        stats=contract_stats,
    )
    expected_chain_id = int(chain["chain_id"])
    if primary_client.chain_id() != expected_chain_id:
        raise ValueError("Primary RPC chain ID mismatch")
    if validation_client.chain_id() != expected_chain_id:
        raise ValueError("Validation RPC chain ID mismatch")
    if contract_client.chain_id() != expected_chain_id:
        raise ValueError("Contract-validation RPC chain ID mismatch")

    calendar_path = project / window["calendar"]
    calendar = read_cohort_calendar(
        calendar_path,
        cohort_id=cohort_id,
        minimum_event_week=int(window["minimum_event_week"]),
        maximum_event_week=int(window["maximum_event_week"]),
    )
    activation_block = int(calendar[0]["activation_block"])
    activation_utc = parse_utc(str(calendar[0]["activation_utc"]))
    output_directory = project / retrieval["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)
    boundary_path = output_directory / "block_boundaries.csv"
    targets = boundary_targets(calendar)
    if resume and boundary_path.exists():
        boundaries = read_and_validate_boundary_cache(boundary_path, targets)
        boundary_mode = "validated_cache"
        print(f"reused {len(boundaries)} internally validated boundaries", flush=True)
    else:
        boundaries = resolve_boundaries(
            primary_client,
            targets,
            activation_block=activation_block,
            activation_utc=activation_utc,
            workers=int(retrieval["boundary_workers"]),
            seed_seconds_per_block=int(retrieval["boundary_seed_seconds_per_block"]),
            initial_radius_blocks=int(retrieval["boundary_initial_radius_blocks"]),
        )
        write_csv_records(boundary_path, boundaries)
        boundary_mode = "rpc_exact_adjacent_headers"

    boundary_checks = cross_provider_boundary_checks(
        boundaries,
        primary_client,
        validation_client,
    )
    boundary_check_path = output_directory / "boundary_provider_checks.csv"
    write_csv_records(boundary_check_path, boundary_checks)

    pool_address = str(config["pool"]["address"]).lower()
    primary_code = contract_client.code(pool_address, activation_block)
    secondary_code = validation_client.code(pool_address, activation_block)
    if primary_code == "0x" or primary_code != secondary_code:
        raise ValueError("Pool bytecode is absent or differs across providers")
    contract_check = {
        "address": pool_address,
        "block_number": activation_block,
        "primary_source_id": chain["contract_validation_rpc"]["source_id"],
        "validation_source_id": chain["validation_rpc"]["source_id"],
        "primary_code_sha256": hashlib.sha256(bytes.fromhex(primary_code[2:])).hexdigest(),
        "validation_code_sha256": hashlib.sha256(bytes.fromhex(secondary_code[2:])).hexdigest(),
        "exact_match": True,
    }
    contract_check_path = output_directory / "contract_code_check.json"
    contract_check_path.write_text(
        json.dumps(contract_check, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if boundaries_only:
        return {
            "boundaries": boundary_path,
            "boundary_checks": boundary_check_path,
            "contract_check": contract_check_path,
        }

    first_block = int(boundaries[0]["start_block"])
    last_block = int(boundaries[-1]["start_block"]) - 1
    chunks = build_chunks(
        first_block,
        last_block,
        int(retrieval["maximum_blocks_per_request"]),
    )
    chunk_directory = project / retrieval["raw_chunk_directory"]
    topics = event_topics()
    raw_logs, chunk_records = fetch_log_chunks(
        primary_client,
        chunks,
        pool_address=pool_address,
        topics=topics,
        chunk_directory=chunk_directory,
        project_root=project,
        workers=int(retrieval["log_workers"]),
        resume=resume,
    )
    chunk_manifest_path = output_directory / "retrieval_chunks.csv"
    write_csv_records(chunk_manifest_path, chunk_records)

    decoded = [
        decode_pool_log(log, chain_id=expected_chain_id, pool_address=pool_address)
        for log in raw_logs
    ]
    decoded = assign_event_weeks(decoded, boundaries)
    processed_path = project / retrieval["processed_event_path"]
    _write_processed_events(processed_path, decoded)

    weekly = build_weekly_action_panel(decoded, calendar)
    reserve_weekly = build_reserve_week_action_panel(decoded, calendar)
    weekly_path = output_directory / "weekly_action_panel.csv"
    reserve_weekly_path = output_directory / "reserve_week_action_panel.csv"
    write_csv_records(weekly_path, weekly)
    write_csv_records(reserve_weekly_path, reserve_weekly)

    checks = cross_provider_checks(
        raw_logs,
        validation_client,
        pool_address=pool_address,
        topics=topics,
        sample_count=int(retrieval["validation_sample_count"]),
        sample_width=int(retrieval["validation_sample_width_blocks"]),
        minimum_block=first_block,
        maximum_block=last_block,
    )
    check_path = output_directory / "cross_provider_checks.csv"
    write_csv_records(check_path, checks)

    action_counts = Counter(record["action"] for record in decoded)
    unique_addresses = {
        str(record[field]).lower()
        for record in decoded
        for field in ("actor_address", "beneficiary_address", "counterparty_address")
        if record.get(field)
    }
    summary = {
        "schema_version": 1,
        "status": "audited_descriptive_panel_input",
        "cohort_id": cohort_id,
        "chain_id": expected_chain_id,
        "pool_address": pool_address,
        "window": {
            "minimum_event_week": int(window["minimum_event_week"]),
            "maximum_event_week": int(window["maximum_event_week"]),
            "start_utc": boundaries[0]["target_utc"],
            "end_utc_exclusive": boundaries[-1]["target_utc"],
            "first_block": first_block,
            "last_block": last_block,
        },
        "event_count": len(decoded),
        "transaction_count": len({record["tx_hash"] for record in decoded}),
        "unique_address_count": len(unique_addresses),
        "reserve_count": len({record["reserve_address"] for record in decoded}),
        "action_counts": dict(sorted(action_counts.items())),
        "weekly_panel_rows": len(weekly),
        "reserve_week_panel_rows": len(reserve_weekly),
        "retrieval_chunk_count": len(chunks),
        "cross_provider_sample_count": len(checks),
        "all_cross_provider_samples_match": all(row["exact_match"] for row in checks),
        "boundary_provider_sample_count": len(boundary_checks),
        "all_boundary_provider_samples_match": all(row["exact_match"] for row in boundary_checks),
        "causal_estimate_produced": False,
        "measurement_level": "address",
        "limitations": [
            "Addresses are not natural persons or economically distinct entities.",
            "Count-based action measures do not measure dollar value.",
            "Reserve-native integer amounts are never summed across assets.",
            "This extraction does not estimate a treatment effect.",
        ],
    }
    summary_path = output_directory / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tracked_artifacts = [
        boundary_path,
        boundary_check_path,
        contract_check_path,
        chunk_manifest_path,
        processed_path,
        weekly_path,
        reserve_weekly_path,
        check_path,
        summary_path,
    ]
    manifest = {
        "schema_version": 2,
        "pipeline": "real_v2_ethereum_aave_v3_pool_actions",
        "generated_at": utc_now_iso(),
        "extraction_source_revision": source_revision(project),
        "path_policy": "repository_relative_posix",
        "configuration": {
            "path": configuration_path.relative_to(project).as_posix(),
            "sha256": sha256_file(configuration_path),
        },
        "calendar": {
            "path": calendar_path.relative_to(project).as_posix(),
            "sha256": sha256_file(calendar_path),
        },
        "providers": {
            "primary_source_id": chain["primary_rpc"]["source_id"],
            "primary_endpoint": safe_rpc_endpoint(primary_url),
            "validation_source_id": chain["validation_rpc"]["source_id"],
            "validation_endpoint": safe_rpc_endpoint(secondary_url),
            "contract_validation_source_id": chain["contract_validation_rpc"]["source_id"],
            "contract_validation_endpoint": safe_rpc_endpoint(contract_url),
        },
        "rpc_statistics": {
            "primary": stats.to_dict(),
            "validation": validation_stats.to_dict(),
            "contract_validation": contract_stats.to_dict(),
        },
        "boundary_retrieval_mode": boundary_mode,
        "contract_check": contract_check,
        "boundary_checks": boundary_checks,
        "raw_log_canonical_sha256": canonical_json_sha256(raw_logs),
        "processed_event_canonical_csv_sha256": gzip_payload_sha256(processed_path),
        "raw_chunks": chunk_records,
        "artifacts": {
            path.relative_to(project).as_posix(): sha256_file(path) for path in tracked_artifacts
        },
        "source_code": {
            relative: sha256_file(project / relative)
            for relative in [
                "src/aave_bns/evm_rpc.py",
                "src/aave_bns/aave_v3_events.py",
                "src/aave_bns/real_v2_ethereum.py",
                "scripts/run_real_v2_ethereum.py",
            ]
        },
        "causal_estimate_produced": False,
    }
    manifest_path = output_directory / "manifest.json"
    write_manifest(manifest_path, manifest)
    return {
        "boundaries": boundary_path,
        "boundary_checks": boundary_check_path,
        "contract_check": contract_check_path,
        "chunks": chunk_manifest_path,
        "processed_events": processed_path,
        "weekly_panel": weekly_path,
        "reserve_week_panel": reserve_weekly_path,
        "cross_provider_checks": check_path,
        "summary": summary_path,
        "manifest": manifest_path,
    }
