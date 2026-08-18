import pandas as pd
import pytest

from aave_bns.bridge_route_gate import (
    INTEGER_PROVENANCE_COLUMNS,
    REQUIRED_COLUMNS,
    WEI_COLUMNS,
    validate_route_events,
)


def complete_row(route_id="1:pool-a->42161:pool-b", suffix="1"):
    row = {column: f"value-{suffix}" for column in REQUIRED_COLUMNS}
    row.update(
        protocol="chainlink_ccip",
        source_chain_id=1,
        destination_chain_id=42161,
        source_log_index=int(suffix),
        destination_log_index=int(suffix),
        lane_activation_block=1,
        amount_wei=1,
        capacity_wei=2,
        route_id=route_id,
        verification_status="onchain_verified",
    )
    return row


def two_routes():
    return [
        complete_row(suffix="1"),
        complete_row(route_id="1:pool-c->42161:pool-d", suffix="2"),
    ]


def test_empty_schema_fails_closed():
    result = validate_route_events(pd.DataFrame(columns=REQUIRED_COLUMNS))
    assert result["bridge_route_gate_passed"] is False
    assert result["infrastructure_dependence_result_produced"] is False


def test_incomplete_provenance_fails_closed_without_duplicate_nan_error():
    rows = two_routes()
    rows[0]["source_tx_hash"] = None
    rows[1]["source_tx_hash"] = None
    result = validate_route_events(pd.DataFrame(rows))
    assert result["bridge_route_gate_passed"] is False
    assert result["withheld_reason"] == "incomplete_message_or_contract_provenance"


@pytest.mark.parametrize("field", INTEGER_PROVENANCE_COLUMNS + WEI_COLUMNS)
def test_fractional_numeric_provenance_fails_closed(field):
    rows = two_routes()
    rows[0][field] = "1.5"
    result = validate_route_events(pd.DataFrame(rows))
    assert result["bridge_route_gate_passed"] is False
    assert result["withheld_reason"] == "incomplete_message_or_contract_provenance"
    assert result["verified_rows"] == 1


def test_zero_lane_activation_block_fails_closed():
    rows = two_routes()
    rows[0]["lane_activation_block"] = 0
    result = validate_route_events(pd.DataFrame(rows))
    assert result["bridge_route_gate_passed"] is False
    assert result["withheld_reason"] == "incomplete_message_or_contract_provenance"


def test_zero_log_indices_remain_valid():
    rows = two_routes()
    for row in rows:
        row["source_log_index"] = 0
        row["destination_log_index"] = 0
    result = validate_route_events(pd.DataFrame(rows))
    assert result["bridge_route_gate_passed"] is True


def test_single_route_stays_closed_even_when_rows_are_verified():
    result = validate_route_events(pd.DataFrame([complete_row()]))
    assert result["bridge_route_gate_passed"] is False
    assert result["withheld_reason"] == "fewer_than_two_independently_identified_routes"


def test_two_complete_independent_routes_pass_gate_only():
    result = validate_route_events(pd.DataFrame(two_routes()))
    assert result["bridge_route_gate_passed"] is True
    assert result["verified_route_count"] == 2
    assert result["infrastructure_dependence_result_produced"] is False


def test_duplicate_source_logs_raise():
    row = complete_row()
    with pytest.raises(ValueError, match="duplicate source"):
        validate_route_events(pd.DataFrame([row, row]))
