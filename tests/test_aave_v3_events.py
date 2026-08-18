from aave_bns.aave_v3_events import EVENT_BY_TOPIC, decode_pool_log

POOL = "0x" + "9" * 40


def word_uint(value: int) -> str:
    return f"{value:064x}"


def word_address(value: str) -> str:
    return "0" * 24 + value.removeprefix("0x").lower()


def topic_address(value: str) -> str:
    return "0x" + word_address(value)


def base_log(topic0: str, topics: list[str], data_words: list[str]) -> dict[str, object]:
    return {
        "address": POOL,
        "topics": [topic0, *topics],
        "data": "0x" + "".join(data_words),
        "blockNumber": "0x64",
        "blockHash": "0x" + "a" * 64,
        "transactionHash": "0x" + "b" * 64,
        "transactionIndex": "0x2",
        "logIndex": "0x3",
        "removed": False,
    }


def test_decode_supply_roles_and_amount():
    reserve = "0x" + "1" * 40
    beneficiary = "0x" + "2" * 40
    user = "0x" + "3" * 40
    topic0 = next(topic for topic, spec in EVENT_BY_TOPIC.items() if spec.action == "supply")
    raw = base_log(
        topic0,
        [topic_address(reserve), topic_address(beneficiary), "0x" + word_uint(7)],
        [word_address(user), word_uint(125)],
    )
    row = decode_pool_log(raw, chain_id=1, pool_address=POOL)
    assert row["action"] == "supply"
    assert row["reserve_address"] == reserve
    assert row["actor_address"] == user
    assert row["beneficiary_address"] == beneficiary
    assert row["amount_raw"] == "125"
    assert row["referral_code"] == 7


def test_decode_liquidation_preserves_both_assets_and_amounts():
    collateral = "0x" + "4" * 40
    debt = "0x" + "5" * 40
    user = "0x" + "6" * 40
    liquidator = "0x" + "7" * 40
    topic0 = next(topic for topic, spec in EVENT_BY_TOPIC.items() if spec.action == "liquidation")
    raw = base_log(
        topic0,
        [topic_address(collateral), topic_address(debt), topic_address(user)],
        [word_uint(80), word_uint(90), word_address(liquidator), word_uint(1)],
    )
    row = decode_pool_log(raw, chain_id=1, pool_address=POOL)
    assert row["reserve_address"] == debt
    assert row["secondary_reserve_address"] == collateral
    assert row["actor_address"] == liquidator
    assert row["beneficiary_address"] == user
    assert row["amount_raw"] == "80"
    assert row["secondary_amount_raw"] == "90"
    assert row["receive_a_token"] is True


def test_decode_rejects_malformed_topic_count():
    topic0 = next(topic for topic, spec in EVENT_BY_TOPIC.items() if spec.action == "repay")
    raw = base_log(topic0, [], [word_uint(1), word_uint(0)])
    try:
        decode_pool_log(raw, chain_id=1, pool_address=POOL)
    except ValueError as exc:
        assert "topic count" in str(exc)
    else:
        raise AssertionError("Malformed event should fail")
