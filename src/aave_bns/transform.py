from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_TRANSFER_COLUMNS = {
    "timestamp",
    "chain_id",
    "asset",
    "token_address",
    "from_address",
    "to_address",
    "value",
}


def normalize_address(value: object) -> str:
    text = str(value).strip().lower()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError(f"Invalid EVM address: {value!r}")
    return text


def validate_transfers(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_TRANSFER_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing transfer columns: {sorted(missing)}")
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="raise")
    out["chain_id"] = pd.to_numeric(out["chain_id"], errors="raise").astype("int64")
    out["value"] = pd.to_numeric(out["value"], errors="raise").astype("float64")
    if (~np.isfinite(out["value"])).any() or (out["value"] < 0).any():
        raise ValueError("Transfer values must be finite and non-negative")
    for column in ("token_address", "from_address", "to_address"):
        out[column] = out[column].map(normalize_address)
    out["asset"] = out["asset"].astype(str).str.upper().str.strip()
    return out.sort_values(["timestamp", "chain_id", "asset"]).reset_index(drop=True)


def apply_entity_map(transfers: pd.DataFrame, mapping: pd.DataFrame | None) -> pd.DataFrame:
    out = transfers.copy()
    if mapping is None or mapping.empty:
        out["from_entity"] = out["from_address"]
        out["to_entity"] = out["to_address"]
        out["entity_resolution"] = "address_level"
        return out

    required = {"address", "entity_id", "confidence"}
    missing = required.difference(mapping.columns)
    if missing:
        raise ValueError(f"Missing entity-map columns: {sorted(missing)}")
    map_frame = mapping.copy()
    map_frame["address"] = map_frame["address"].map(normalize_address)
    if map_frame["address"].duplicated().any():
        raise ValueError("Entity map contains duplicate addresses")

    lookup = map_frame.set_index("address")["entity_id"].astype(str)
    out["from_entity"] = out["from_address"].map(lookup).fillna(out["from_address"])
    out["to_entity"] = out["to_address"].map(lookup).fillna(out["to_address"])
    out["entity_resolution"] = "mapped_with_address_fallback"
    return out


def read_transfers(path: str | Path) -> pd.DataFrame:
    return validate_transfers(pd.read_csv(path))
