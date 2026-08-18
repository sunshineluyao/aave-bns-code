from aave_bns.ccip_route_extract import canonical_hash, pair_message_events


def test_pairs_only_equal_message_ids_and_keeps_gate_closed():
    source = [{"message_id": "0xa", "tx_hash": "0x1", "log_index": 2}]
    destination = [
        {"message_id": "0xa", "tx_hash": "0x2", "log_index": 3, "block_number": 4}
    ]
    paired, audit = pair_message_events(source, destination)
    assert paired[0]["destination_tx_hash"] == "0x2"
    assert audit["paired_event_count"] == 1
    assert audit["bridge_route_gate_passed"] is False
    assert audit["first_transfer_claim_permitted"] is False


def test_duplicate_destination_identity_is_not_silently_paired():
    source = [{"message_id": "0xa"}]
    destination = [{"message_id": "0xa"}, {"message_id": "0xa"}]
    paired, audit = pair_message_events(source, destination)
    assert paired == []
    assert audit["duplicate_destination_message_ids"] == ["0xa"]


def test_canonical_hash_is_key_order_independent():
    assert canonical_hash([{"a": 1, "b": 2}]) == canonical_hash([{"b": 2, "a": 1}])
