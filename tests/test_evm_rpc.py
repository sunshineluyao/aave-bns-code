from datetime import datetime, timezone

import pytest

from aave_bns.evm_rpc import RpcClient, RpcError, resolve_first_block_at_or_after


class FakeClient:
    def __init__(self, timestamps: list[int]):
        self.timestamps = timestamps

    def block(self, block_number: int):
        return {
            "number": hex(block_number),
            "timestamp": hex(self.timestamps[block_number]),
            "hash": "0x" + f"{block_number:064x}",
        }


def test_exact_boundary_uses_first_eligible_block():
    client = FakeClient([100, 110, 121, 133, 145])
    target = datetime.fromtimestamp(121, tz=timezone.utc)
    row = resolve_first_block_at_or_after(client, target, low_block=0, high_block=4)
    assert row["start_block"] == 2
    assert row["lag_seconds"] == 0
    assert row["previous_block_timestamp"].endswith("00:01:50Z")


def test_exact_boundary_records_skipped_slot_lag():
    client = FakeClient([100, 110, 125, 137])
    target = datetime.fromtimestamp(120, tz=timezone.utc)
    row = resolve_first_block_at_or_after(client, target, low_block=0, high_block=3)
    assert row["start_block"] == 2
    assert row["lag_seconds"] == 5


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def test_batch_rpc_restores_input_order_from_unordered_response():
    def post(_url, *, json, timeout):
        assert timeout == 3
        assert [item["id"] for item in json] == [1, 2]
        return FakeResponse(
            [
                {"jsonrpc": "2.0", "id": 2, "result": "second"},
                {"jsonrpc": "2.0", "id": 1, "result": "first"},
            ]
        )

    client = RpcClient("https://rpc.example", timeout_seconds=3, post=post)
    results = client.batch_call([("method_a", []), ("method_b", [1])])
    assert results == ["first", "second"]
    assert client.stats.methods == {"method_a": 1, "method_b": 1}


def test_batch_rpc_rejects_duplicate_response_ids():
    def post(_url, *, json, timeout):
        return FakeResponse(
            [
                {"jsonrpc": "2.0", "id": json[0]["id"], "result": "one"},
                {"jsonrpc": "2.0", "id": json[0]["id"], "result": "duplicate"},
            ]
        )

    client = RpcClient("https://rpc.example", maximum_attempts=1, post=post)
    with pytest.raises(RpcError, match="duplicate id"):
        client.batch_call([("method_a", []), ("method_b", [])])
