import pandas as pd

from aave_bns.query import normalize_warehouse_transfers
from aave_bns.transform import validate_transfers


def test_warehouse_query_output_connects_to_analysis_schema():
    address = "0x" + "1" * 40
    frame = pd.DataFrame({
        "timestamp": ["2026-01-01T00:00:00Z"],
        "block_number": [1],
        "tx_hash": ["0x" + "a" * 64],
        "log_index": [0],
        "chain_id": [1],
        "token_address": [address],
        "from_address": ["0x" + "2" * 40],
        "to_address": ["0x" + "3" * 40],
        "raw_value": ["1250000"],
    })
    normalized = normalize_warehouse_transfers(
        frame,
        {"TEST": {"address": address, "decimals": 6}},
    )
    assert normalized.loc[0, "asset"] == "TEST"
    assert normalized.loc[0, "value"] == 1.25
    validated = validate_transfers(normalized)
    assert validated.loc[0, "value"] == 1.25
