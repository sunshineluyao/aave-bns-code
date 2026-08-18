from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = (
    "route_id",
    "protocol",
    "source_chain_id",
    "destination_chain_id",
    "source_token_pool",
    "destination_token_pool",
    "source_router",
    "destination_router",
    "lane_activation_tx",
    "lane_activation_block",
    "message_id",
    "source_tx_hash",
    "source_log_index",
    "destination_tx_hash",
    "destination_log_index",
    "amount_wei",
    "capacity_wei",
    "source_url",
    "verification_status",
)

INTEGER_PROVENANCE_COLUMNS = (
    "source_chain_id",
    "destination_chain_id",
    "source_log_index",
    "destination_log_index",
    "lane_activation_block",
)

WEI_COLUMNS = ("amount_wei", "capacity_wei")

_INTEGER_TEXT = re.compile(r"[+-]?\d+\Z")
_MAX_EXACT_FLOAT_INTEGER = 2**53 - 1


def _present(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.notna() & frame.astype("string").apply(
        lambda column: column.str.strip().ne("")
    )


def _exact_integer(value: object) -> int | None:
    """Return an exact integer or fail closed for fractional/lossy values."""
    if value is None or value is pd.NA or isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not _INTEGER_TEXT.fullmatch(stripped):
            return None
        return int(stripped)
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            return None
        return int(value)
    if isinstance(value, Real):
        numeric = float(value)
        if (
            not math.isfinite(numeric)
            or not numeric.is_integer()
            or abs(numeric) > _MAX_EXACT_FLOAT_INTEGER
        ):
            return None
        return int(numeric)
    return None


def _exact_integers(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    return frame.loc[:, columns].map(_exact_integer)


def validate_route_events(frame: pd.DataFrame) -> dict[str, object]:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Bridge-route input is missing columns: {missing}")
    if frame.empty:
        return _closed("no_message_level_route_events")

    required_present = _present(frame[list(REQUIRED_COLUMNS)]).all(axis=1)
    provenance = _exact_integers(frame, INTEGER_PROVENANCE_COLUMNS)
    wei = _exact_integers(frame, WEI_COLUMNS)
    numeric_valid = (
        provenance.notna().all(axis=1)
        & provenance.ge(0).all(axis=1)
        & provenance["lane_activation_block"].gt(0)
        & wei.notna().all(axis=1)
        & wei["amount_wei"].gt(0)
        & wei["capacity_wei"].ge(0)
    )
    verified = frame["verification_status"].eq("onchain_verified")
    complete = required_present & numeric_valid & verified
    if not complete.all():
        return {
            **_closed("incomplete_message_or_contract_provenance"),
            "event_rows": int(len(frame)),
            "verified_rows": int(complete.sum()),
        }

    source_key = ["source_chain_id", "source_tx_hash", "source_log_index"]
    destination_key = [
        "destination_chain_id",
        "destination_tx_hash",
        "destination_log_index",
    ]
    if frame[source_key].duplicated().any():
        raise ValueError("Bridge-route input contains duplicate source log keys")
    if frame[destination_key].duplicated().any():
        raise ValueError("Bridge-route input contains duplicate destination log keys")
    if frame["message_id"].duplicated().any():
        raise ValueError("Bridge-route input contains duplicate message IDs")

    route_count = int(frame["route_id"].nunique())
    if route_count < 2:
        return {
            **_closed("fewer_than_two_independently_identified_routes"),
            "event_rows": int(len(frame)),
            "verified_rows": int(len(frame)),
            "verified_route_count": route_count,
        }
    return {
        "schema_version": 1,
        "status": "verified_message_level_routes",
        "bridge_route_gate_passed": True,
        "infrastructure_dependence_result_produced": False,
        "event_rows": int(len(frame)),
        "verified_rows": int(len(frame)),
        "verified_route_count": route_count,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }


def _closed(reason: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "withheld",
        "bridge_route_gate_passed": False,
        "infrastructure_dependence_result_produced": False,
        "withheld_reason": reason,
        "entity_level_primary_result_produced": False,
        "causal_estimate_produced": False,
    }


def audit_route_file(input_path: str | Path, output_path: str | Path) -> Path:
    result = validate_route_events(pd.read_csv(input_path))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output
