from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evm_rpc import int_from_hex


@dataclass(frozen=True)
class EventSpec:
    action: str
    signature: str
    topic0: str
    indexed_fields: tuple[tuple[str, str], ...]
    data_fields: tuple[tuple[str, str], ...]


EVENT_SPECS = (
    EventSpec(
        action="supply",
        signature="Supply(address,address,address,uint256,uint16)",
        topic0="0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61",
        indexed_fields=(
            ("reserve", "address"),
            ("on_behalf_of", "address"),
            ("referral_code", "uint16"),
        ),
        data_fields=(("user", "address"), ("amount", "uint256")),
    ),
    EventSpec(
        action="withdraw",
        signature="Withdraw(address,address,address,uint256)",
        topic0="0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7",
        indexed_fields=(("reserve", "address"), ("user", "address"), ("to", "address")),
        data_fields=(("amount", "uint256"),),
    ),
    EventSpec(
        action="borrow",
        signature="Borrow(address,address,address,uint256,uint8,uint256,uint16)",
        topic0="0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0",
        indexed_fields=(
            ("reserve", "address"),
            ("on_behalf_of", "address"),
            ("referral_code", "uint16"),
        ),
        data_fields=(
            ("user", "address"),
            ("amount", "uint256"),
            ("interest_rate_mode", "uint8"),
            ("borrow_rate", "uint256"),
        ),
    ),
    EventSpec(
        action="repay",
        signature="Repay(address,address,address,uint256,bool)",
        topic0="0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051",
        indexed_fields=(("reserve", "address"), ("user", "address"), ("repayer", "address")),
        data_fields=(("amount", "uint256"), ("use_a_tokens", "bool")),
    ),
    EventSpec(
        action="liquidation",
        signature="LiquidationCall(address,address,address,uint256,uint256,address,bool)",
        topic0="0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286",
        indexed_fields=(
            ("collateral_asset", "address"),
            ("debt_asset", "address"),
            ("user", "address"),
        ),
        data_fields=(
            ("debt_to_cover", "uint256"),
            ("liquidated_collateral_amount", "uint256"),
            ("liquidator", "address"),
            ("receive_a_token", "bool"),
        ),
    ),
)

EVENT_BY_TOPIC = {spec.topic0: spec for spec in EVENT_SPECS}


def _decode_word(word: str, abi_type: str) -> str | int | bool:
    if len(word) != 64:
        raise ValueError(f"ABI word must have 64 hexadecimal characters, got {len(word)}")
    try:
        integer = int(word, 16)
    except ValueError as exc:
        raise ValueError("ABI word is not hexadecimal") from exc
    if abi_type == "address":
        if integer >= 2**160:
            raise ValueError("Address ABI word has nonzero high-order bits")
        return "0x" + word[-40:].lower()
    if abi_type == "bool":
        if integer not in (0, 1):
            raise ValueError(f"Invalid ABI boolean value: {integer}")
        return bool(integer)
    if abi_type.startswith("uint"):
        bits = int(abi_type.removeprefix("uint") or "256")
        if integer >= 2**bits:
            raise ValueError(f"Value exceeds {abi_type}")
        return integer
    raise ValueError(f"Unsupported ABI type: {abi_type}")


def _data_words(data: str) -> list[str]:
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("Event data must be 0x-prefixed")
    encoded = data[2:]
    if len(encoded) % 64:
        raise ValueError("Event data is not aligned to 32-byte ABI words")
    return [encoded[index : index + 64] for index in range(0, len(encoded), 64)]


def _topic_word(topic: str) -> str:
    if not isinstance(topic, str) or not topic.startswith("0x") or len(topic) != 66:
        raise ValueError(f"Malformed event topic: {topic!r}")
    return topic[2:]


def decode_pool_log(raw: dict[str, Any], *, chain_id: int, pool_address: str) -> dict[str, Any]:
    topics = raw.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("Log has no topics")
    topic0 = str(topics[0]).lower()
    if topic0 not in EVENT_BY_TOPIC:
        raise ValueError(f"Unknown Aave V3 Pool event topic: {topic0}")
    spec = EVENT_BY_TOPIC[topic0]
    if len(topics) != len(spec.indexed_fields) + 1:
        raise ValueError(f"{spec.action} log has an unexpected topic count")

    values: dict[str, str | int | bool] = {}
    for topic, (field, abi_type) in zip(topics[1:], spec.indexed_fields, strict=True):
        values[field] = _decode_word(_topic_word(str(topic)), abi_type)

    words = _data_words(str(raw.get("data", "")))
    if len(words) != len(spec.data_fields):
        raise ValueError(f"{spec.action} log has an unexpected data-word count")
    for word, (field, abi_type) in zip(words, spec.data_fields, strict=True):
        values[field] = _decode_word(word, abi_type)

    row: dict[str, Any] = {
        "chain_id": chain_id,
        "pool_address": pool_address.lower(),
        "action": spec.action,
        "event_signature": spec.signature,
        "topic0": topic0,
        "block_number": int_from_hex(str(raw["blockNumber"])),
        "block_hash": str(raw["blockHash"]).lower(),
        "tx_hash": str(raw["transactionHash"]).lower(),
        "tx_index": int_from_hex(str(raw.get("transactionIndex", "0x0"))),
        "log_index": int_from_hex(str(raw["logIndex"])),
        "reserve_address": "",
        "secondary_reserve_address": "",
        "actor_address": "",
        "beneficiary_address": "",
        "counterparty_address": "",
        "amount_raw": "",
        "secondary_amount_raw": "",
        "referral_code": "",
        "interest_rate_mode": "",
        "borrow_rate_ray": "",
        "use_a_tokens": "",
        "receive_a_token": "",
    }

    if spec.action == "supply":
        row.update(
            reserve_address=values["reserve"],
            actor_address=values["user"],
            beneficiary_address=values["on_behalf_of"],
            amount_raw=str(values["amount"]),
            referral_code=values["referral_code"],
        )
    elif spec.action == "withdraw":
        row.update(
            reserve_address=values["reserve"],
            actor_address=values["user"],
            beneficiary_address=values["user"],
            counterparty_address=values["to"],
            amount_raw=str(values["amount"]),
        )
    elif spec.action == "borrow":
        row.update(
            reserve_address=values["reserve"],
            actor_address=values["user"],
            beneficiary_address=values["on_behalf_of"],
            amount_raw=str(values["amount"]),
            referral_code=values["referral_code"],
            interest_rate_mode=values["interest_rate_mode"],
            borrow_rate_ray=str(values["borrow_rate"]),
        )
    elif spec.action == "repay":
        row.update(
            reserve_address=values["reserve"],
            actor_address=values["repayer"],
            beneficiary_address=values["user"],
            amount_raw=str(values["amount"]),
            use_a_tokens=values["use_a_tokens"],
        )
    elif spec.action == "liquidation":
        row.update(
            reserve_address=values["debt_asset"],
            secondary_reserve_address=values["collateral_asset"],
            actor_address=values["liquidator"],
            beneficiary_address=values["user"],
            amount_raw=str(values["debt_to_cover"]),
            secondary_amount_raw=str(values["liquidated_collateral_amount"]),
            receive_a_token=values["receive_a_token"],
        )
    else:  # pragma: no cover - protected by the fixed registry above
        raise AssertionError(f"Unhandled event action: {spec.action}")
    return row


def event_topics() -> list[str]:
    return [spec.topic0 for spec in EVENT_SPECS]
