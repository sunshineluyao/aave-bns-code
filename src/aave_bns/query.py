from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from .provenance import sha256_file, utc_now_iso, write_manifest

WAREHOUSE_REQUIRED_COLUMNS = {
    "timestamp",
    "block_number",
    "tx_hash",
    "log_index",
    "chain_id",
    "token_address",
    "from_address",
    "to_address",
    "raw_value",
}


def normalize_warehouse_transfers(
    frame: pd.DataFrame,
    asset_metadata: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Convert warehouse-native integer token values into the pipeline schema.

    ``asset_metadata`` is keyed by symbol and must provide ``address`` and
    ``decimals`` for every queried asset. The raw integer value is preserved so
    source records remain auditable after decimal normalization.
    """
    missing = WAREHOUSE_REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Warehouse result is missing columns: {sorted(missing)}")

    by_address: dict[str, tuple[str, int]] = {}
    for symbol, metadata in asset_metadata.items():
        address = str(metadata["address"]).lower()
        decimals = int(metadata["decimals"])
        if address in by_address:
            raise ValueError(f"Duplicate token address in asset metadata: {address}")
        by_address[address] = (str(symbol).upper(), decimals)

    out = frame.copy()
    out["token_address"] = out["token_address"].astype(str).str.lower()
    unknown = sorted(set(out["token_address"]).difference(by_address))
    if unknown:
        raise ValueError(f"Warehouse result contains unconfigured token addresses: {unknown}")

    out["asset"] = out["token_address"].map(lambda address: by_address[address][0])

    def scale(row: pd.Series) -> float:
        decimals = by_address[row["token_address"]][1]
        try:
            return float(Decimal(str(row["raw_value"])) / (Decimal(10) ** decimals))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Invalid raw token value: {row['raw_value']!r}") from exc

    out["value"] = out.apply(scale, axis=1)
    columns = [
        "timestamp",
        "block_number",
        "tx_hash",
        "log_index",
        "chain_id",
        "asset",
        "token_address",
        "from_address",
        "to_address",
        "raw_value",
        "value",
    ]
    return out[columns]


def query_bigquery_token_transfers(
    *,
    sql_path: str | Path,
    output_path: str | Path,
    project: str,
    start_date: str,
    end_date: str,
    asset_metadata: dict[str, dict[str, Any]],
) -> Path:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError("Install query dependencies with: pip install -e '.[query]'") from exc

    sql_file = Path(sql_path)
    query = sql_file.read_text(encoding="utf-8")
    token_addresses = [str(metadata["address"]).lower() for metadata in asset_metadata.values()]
    client = bigquery.Client(project=project)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
            bigquery.ArrayQueryParameter("token_addresses", "STRING", token_addresses),
        ]
    )
    raw_frame = client.query(query, job_config=job_config).to_dataframe()
    frame = normalize_warehouse_transfers(raw_frame, asset_metadata)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False)
    write_manifest(
        destination.with_suffix(destination.suffix + ".manifest.json"),
        {
            "source": "bigquery-public-data.crypto_ethereum.token_transfers",
            "query_file": str(sql_file),
            "query_sha256": sha256_file(sql_file),
            "output_sha256": sha256_file(destination),
            "parameters": {
                "start_date": start_date,
                "end_date": end_date,
                "assets": {
                    symbol: {
                        "address": str(metadata["address"]).lower(),
                        "decimals": int(metadata["decimals"]),
                    }
                    for symbol, metadata in sorted(asset_metadata.items())
                },
                "billing_project": project,
            },
            "retrieved_at": utc_now_iso(),
        },
    )
    return destination


def rpc_get_logs(
    *,
    rpc_url: str,
    contract_address: str,
    topic0: str,
    from_block: int,
    to_block: int,
    output_path: str | Path,
) -> Path:
    import requests

    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "address": contract_address,
            "topics": [topic0],
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }],
    }
    response = requests.post(rpc_url, json=payload, timeout=120)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"RPC error: {body['error']}")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(body["result"], indent=2) + "\n", encoding="utf-8")
    write_manifest(
        destination.with_suffix(destination.suffix + ".manifest.json"),
        {
            "source": "evm_json_rpc.eth_getLogs",
            "contract_address": contract_address.lower(),
            "topic0": topic0.lower(),
            "from_block": from_block,
            "to_block": to_block,
            "output_sha256": sha256_file(destination),
            "retrieved_at": utc_now_iso(),
        },
    )
    return destination
