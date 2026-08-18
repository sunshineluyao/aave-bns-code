from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests


class RpcError(RuntimeError):
    """Raised when a JSON-RPC request cannot be completed or validated."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def int_from_hex(value: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"Expected a 0x-prefixed hexadecimal value, got {value!r}")
    return int(value, 16)


@dataclass
class RpcStats:
    requests: int = 0
    retries: int = 0
    errors: int = 0
    methods: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_request(self, method: str) -> None:
        with self._lock:
            self.requests += 1
            self.methods[method] = self.methods.get(method, 0) + 1

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": self.requests,
                "retries": self.retries,
                "errors": self.errors,
                "methods": dict(sorted(self.methods.items())),
            }


class RpcClient:
    """Small retrying JSON-RPC client with strict response validation."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 120,
        maximum_attempts: int = 6,
        stats: RpcStats | None = None,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if maximum_attempts < 1:
            raise ValueError("maximum_attempts must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.maximum_attempts = maximum_attempts
        self.stats = stats or RpcStats()
        self._post = post
        self._sleep = sleep
        self._id_lock = threading.Lock()
        self._next_id = 1

    def _request_id(self) -> int:
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

    def call(self, method: str, params: list[Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.maximum_attempts + 1):
            request_id = self._request_id()
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            self.stats.record_request(method)
            try:
                response = self._post(
                    self.url,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise RpcError(f"{method} returned a non-object JSON response")
                if body.get("id") != request_id:
                    raise RpcError(
                        f"{method} returned response id {body.get('id')!r}; expected {request_id}"
                    )
                if "error" in body:
                    raise RpcError(f"{method} RPC error: {body['error']}")
                if "result" not in body:
                    raise RpcError(f"{method} response omitted result")
                return body["result"]
            except (requests.RequestException, ValueError, RpcError) as exc:
                last_error = exc
                self.stats.record_error()
                if attempt == self.maximum_attempts:
                    break
                self.stats.record_retry()
                delay = min(8.0, 0.35 * (2 ** (attempt - 1)))
                delay += random.Random(f"{method}:{request_id}").uniform(0.0, 0.15)
                self._sleep(delay)
        raise RpcError(
            f"{method} failed after {self.maximum_attempts} attempts at {self.url}: {last_error}"
        ) from last_error

    def batch_call(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        """Execute an ordered JSON-RPC batch and return results in input order.

        Ethereum JSON-RPC permits providers to return batch responses in any order.  This
        method therefore validates every response id, rejects missing or duplicate ids, and
        reconstructs the caller's order explicitly.  A provider-side error in any member
        fails the whole batch so that a partially classified address release cannot be
        mistaken for a complete one.
        """
        if not calls:
            return []

        last_error: Exception | None = None
        for attempt in range(1, self.maximum_attempts + 1):
            request_ids = [self._request_id() for _ in calls]
            payload = []
            for request_id, (method, params) in zip(request_ids, calls, strict=True):
                self.stats.record_request(method)
                payload.append(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }
                )
            try:
                response = self._post(
                    self.url,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, list):
                    raise RpcError("JSON-RPC batch returned a non-list response")

                expected = set(request_ids)
                by_id: dict[int, Any] = {}
                for item in body:
                    if not isinstance(item, dict):
                        raise RpcError("JSON-RPC batch contained a non-object response")
                    response_id = item.get("id")
                    if response_id not in expected:
                        raise RpcError(f"JSON-RPC batch returned unexpected id {response_id!r}")
                    if response_id in by_id:
                        raise RpcError(f"JSON-RPC batch returned duplicate id {response_id!r}")
                    if "error" in item:
                        raise RpcError(
                            f"JSON-RPC batch member {response_id} returned error: {item['error']}"
                        )
                    if "result" not in item:
                        raise RpcError(
                            f"JSON-RPC batch member {response_id} omitted result"
                        )
                    by_id[response_id] = item["result"]
                missing = expected.difference(by_id)
                if missing:
                    raise RpcError(
                        f"JSON-RPC batch omitted response ids {sorted(missing)}"
                    )
                return [by_id[request_id] for request_id in request_ids]
            except (requests.RequestException, ValueError, RpcError) as exc:
                last_error = exc
                self.stats.record_error()
                if attempt == self.maximum_attempts:
                    break
                self.stats.record_retry()
                delay = min(8.0, 0.35 * (2 ** (attempt - 1)))
                delay += random.Random(f"batch:{request_ids[0]}").uniform(0.0, 0.15)
                self._sleep(delay)
        raise RpcError(
            "JSON-RPC batch failed after "
            f"{self.maximum_attempts} attempts at {self.url}: {last_error}"
        ) from last_error

    def chain_id(self) -> int:
        return int_from_hex(self.call("eth_chainId", []))

    def latest_block_number(self) -> int:
        return int_from_hex(self.call("eth_blockNumber", []))

    def block(self, block_number: int) -> dict[str, Any]:
        result = self.call("eth_getBlockByNumber", [hex(block_number), False])
        if result is None:
            raise RpcError(f"Missing block {block_number}")
        if int_from_hex(result["number"]) != block_number:
            raise RpcError(f"Block-number mismatch for {block_number}")
        return result

    def logs(self, filter_parameters: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.call("eth_getLogs", [filter_parameters])
        if not isinstance(result, list):
            raise RpcError("eth_getLogs returned a non-list result")
        return result

    def code(self, address: str, block_number: int) -> str:
        result = self.call("eth_getCode", [address, hex(block_number)])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise RpcError("eth_getCode returned malformed bytecode")
        return result.lower()


def block_timestamp(block: dict[str, Any]) -> int:
    return int_from_hex(block["timestamp"])


def resolve_first_block_at_or_after(
    client: RpcClient,
    target: datetime,
    *,
    low_block: int,
    high_block: int,
) -> dict[str, Any]:
    """Return the exact first block whose timestamp is at least ``target``.

    The search uses only observed block headers. It never converts time to blocks using an
    assumed average block time.
    """
    if target.tzinfo is None:
        raise ValueError("target must be timezone-aware")
    target_timestamp = int(target.astimezone(timezone.utc).timestamp())
    if low_block < 0 or high_block < low_block:
        raise ValueError("Invalid block-search interval")

    header_cache: dict[int, dict[str, Any]] = {}

    def header(block_number: int) -> dict[str, Any]:
        if block_number not in header_cache:
            header_cache[block_number] = client.block(block_number)
        return header_cache[block_number]

    low_header = header(low_block)
    high_header = header(high_block)
    low_timestamp = block_timestamp(low_header)
    high_timestamp = block_timestamp(high_header)
    if high_timestamp < target_timestamp:
        raise ValueError("High block precedes the target timestamp")
    if low_block > 0 and low_timestamp >= target_timestamp:
        previous = header(low_block - 1)
        if block_timestamp(previous) >= target_timestamp:
            raise ValueError("Low block is above the first eligible block")
        first_block = low_block
    elif low_timestamp == target_timestamp:
        first_block = low_block
    else:
        # Maintain a strict observed bracket: left is earlier than the target and right is
        # at or after it. Interpolation uses only timestamps read from those headers. It is
        # a search accelerator, not an assumed block-time conversion; the final adjacent
        # pair is still verified below.
        left = low_block
        right = high_block
        left_timestamp = low_timestamp
        right_timestamp = high_timestamp
        while right - left > 1:
            timestamp_span = right_timestamp - left_timestamp
            if timestamp_span <= 0:
                probe = (left + right) // 2
            else:
                projected = (target_timestamp - left_timestamp) * (right - left) // timestamp_span
                probe = left + projected
                probe = max(left + 1, min(right - 1, probe))
            probe_timestamp = block_timestamp(header(probe))
            if probe_timestamp < target_timestamp:
                left = probe
                left_timestamp = probe_timestamp
            else:
                right = probe
                right_timestamp = probe_timestamp
        first_block = right

    first = header(first_block)
    previous = header(first_block - 1) if first_block > 0 else None
    first_timestamp = block_timestamp(first)
    previous_timestamp = block_timestamp(previous) if previous else None
    if first_timestamp < target_timestamp:
        raise AssertionError("Resolved block is earlier than target")
    if previous_timestamp is not None and previous_timestamp >= target_timestamp:
        raise AssertionError("Resolved block is not the first eligible block")
    return {
        "target_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "start_block": first_block,
        "start_block_timestamp": datetime.fromtimestamp(first_timestamp, tz=timezone.utc)
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
        "lag_seconds": first_timestamp - target_timestamp,
    }


def canonicalize_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for raw in logs:
        item = dict(raw)
        for key in ("address", "blockHash", "transactionHash", "data"):
            if isinstance(item.get(key), str):
                item[key] = item[key].lower()
        if isinstance(item.get("topics"), list):
            item["topics"] = [str(topic).lower() for topic in item["topics"]]
        normalized.append(item)
    return sorted(
        normalized,
        key=lambda row: (
            int_from_hex(row["blockNumber"]),
            int_from_hex(row.get("transactionIndex", "0x0")),
            int_from_hex(row["logIndex"]),
        ),
    )
