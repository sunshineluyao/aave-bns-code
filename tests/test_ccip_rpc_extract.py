import pytest

from aave_bns.ccip_rpc_extract import EventQuery, extract_indexed_message_events


class FakeClient:
    def __init__(self, chain_id=1, code="0x6000", log_updates=None):
        self._chain_id = chain_id
        self._code = code
        self._log_updates = log_updates or {}
        self.filters = []
        self.stats = type("Stats", (), {"to_dict": lambda self: {"requests": 4}})()

    def chain_id(self):
        return self._chain_id

    def code(self, address, block):
        return self._code

    def logs(self, filter_parameters):
        self.filters.append(filter_parameters)
        block = int(filter_parameters["fromBlock"], 16)
        log = {
            "address": filter_parameters["address"],
            "topics": [filter_parameters["topics"][0], "0x" + "ab" * 32],
            "data": "0x",
            "blockHash": "0x" + "11" * 32,
            "blockNumber": hex(block),
            "transactionHash": "0x" + f"{block:064x}",
            "transactionIndex": "0x0",
            "logIndex": "0x0",
            "removed": False,
        }
        omit_removed = self._log_updates.get("_omit_removed", False)
        log.update(
            {key: value for key, value in self._log_updates.items() if key != "_omit_removed"}
        )
        if omit_removed:
            log.pop("removed")
        return [log]


def query():
    return EventQuery(
        chain_id=1,
        contract_address="0x" + "12" * 20,
        topic0="0x" + "34" * 32,
        start_block=10,
        end_block=14,
        message_id_topic_index=1,
        chunk_size=2,
    )


def test_extracts_continuous_chunks_and_keeps_gate_closed():
    client = FakeClient()
    events, audit = extract_indexed_message_events(client, query())
    assert len(events) == 3
    assert audit["continuous_inclusive_coverage"] is True
    assert audit["returned_log_validation_passed"] is True
    assert audit["bridge_route_gate_passed"] is False
    assert audit["first_transfer_claim_permitted"] is False
    assert [(f["fromBlock"], f["toBlock"]) for f in client.filters] == [
        ("0xa", "0xb"), ("0xc", "0xd"), ("0xe", "0xe")
    ]


def test_refuses_wrong_chain():
    with pytest.raises(ValueError, match="chain_id"):
        extract_indexed_message_events(FakeClient(chain_id=42161), query())


def test_refuses_missing_historical_code():
    with pytest.raises(ValueError, match="runtime code"):
        extract_indexed_message_events(FakeClient(code="0x"), query())


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"removed": True}, "removed must be present and strictly false"),
        ({"removed": None}, "removed must be present and strictly false"),
        ({"removed": 0}, "removed must be present and strictly false"),
        ({"_omit_removed": True}, "removed must be present and strictly false"),
        ({"address": "0x" + "99" * 20}, "different contract"),
        ({"topics": ["0x" + "88" * 32, "0x" + "ab" * 32]}, "different topic0"),
        ({"blockNumber": "0x3e8"}, "outside the requested chunk"),
    ],
)
def test_refuses_rpc_logs_that_do_not_match_query(updates, message):
    with pytest.raises(ValueError, match=message):
        extract_indexed_message_events(FakeClient(log_updates=updates), query())


@pytest.mark.parametrize(
    "message_id",
    [
        "ab" * 33,
        "0x" + "zz" * 32,
        "0x" + "ab" * 31,
    ],
)
def test_refuses_malformed_message_id(message_id):
    with pytest.raises(ValueError, match="message_id topic is malformed"):
        extract_indexed_message_events(
            FakeClient(log_updates={"topics": [query().topic0, message_id]}), query()
        )


def test_execution_state_changed_uses_second_indexed_topic_for_message_id():
    execution_query = EventQuery(
        chain_id=1,
        contract_address="0x" + "12" * 20,
        topic0="0xd4f851956a5d67c3997d1c9205045fef79bae2947fdee7e9e2641abc7391ef65",
        start_block=10,
        end_block=10,
        message_id_topic_index=2,
        chunk_size=1,
    )
    sequence_number = "0x" + "00" * 31 + "01"
    message_id = "0x" + "cd" * 32
    client = FakeClient(
        log_updates={
            "topics": [execution_query.topic0, sequence_number, message_id]
        }
    )
    events, audit = extract_indexed_message_events(client, execution_query)
    assert events[0]["message_id"] == message_id
    assert audit["topic0"] == execution_query.topic0
