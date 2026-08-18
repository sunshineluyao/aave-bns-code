import csv
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from aave_bns.real_v6_gnosis_donor import (
    CAUSAL_STATUS,
    build_common_panel,
    estimate_did,
    load_gnosis_donor_config,
    newey_west_ols,
    placebo_diagnostics,
    pretrend_diagnostic,
    read_common_calendar,
    read_donor_boundary_cache,
    render_event_study_svg,
)

CONFIG_PATH = "configs/real_v6_gnosis_donor.yaml"


def _synthetic_weekly(chain_offset: int = 0, post_effect: int = 0) -> pd.DataFrame:
    rows = []
    for week in range(-16, 17):
        active = 100 + week + chain_offset + (post_effect if week >= 1 else 0)
        rows.append(
            {
                "chain": "synthetic",
                "event_week": week,
                "event_count": 2 * active,
                "active_beneficiary_addresses": active,
                "beneficiary_hhi": 0.1 + (0.02 if week >= 1 and post_effect else 0.0),
            }
        )
    return pd.DataFrame(rows)


def test_config_and_common_calendar_are_locked_to_safe_two_chain_design():
    config = load_gnosis_donor_config(CONFIG_PATH)
    calendar = read_common_calendar(config)
    assert [int(row["event_week"]) for row in calendar] == list(range(-16, 17))
    assert all(int(row["chain_id"]) == 100 for row in calendar)
    assert config["design"]["causal_estimates_allowed"] is False
    assert config["release_gate"]["causal_estimates_allowed"] is False


def test_donor_boundary_cache_uses_adjacent_headers_not_arbitrum_exact_week_zero(tmp_path):
    target = datetime(2024, 7, 2, 15, 40, 32, tzinfo=timezone.utc)
    path = tmp_path / "boundaries.csv"
    records = [
        {
            "boundary_event_week": 0,
            "target_utc": target.isoformat().replace("+00:00", "Z"),
            "start_block": 35000000,
            "start_block_timestamp": (target + timedelta(seconds=3))
            .isoformat()
            .replace("+00:00", "Z"),
            "previous_block": 34999999,
            "previous_block_timestamp": (target - timedelta(seconds=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "lag_seconds": 3,
            "start_block_hash": "0x" + "12" * 32,
        }
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    assert read_donor_boundary_cache(path, [(0, target)]) == [
        {key: str(value) for key, value in records[0].items()}
    ]


def test_common_panel_has_exact_calendar_and_exclusion_partition():
    config = load_gnosis_donor_config(CONFIG_PATH)
    calendar = read_common_calendar(config)
    rows = build_common_panel(
        config,
        _synthetic_weekly(),
        _synthetic_weekly(chain_offset=5),
        calendar,
    )
    panel = pd.DataFrame(rows)
    assert len(panel) == 66
    assert set(panel.groupby("chain").size()) == {33}
    assert set(panel.loc[panel.event_week.between(-16, -9), "period"]) == {"clean_pre"}
    assert set(panel.loc[panel.event_week.between(-8, -1), "period"]) == {
        "anticipation_excluded"
    }
    assert set(panel.loc[panel.event_week == 0, "period"]) == {
        "event_week_zero_excluded"
    }
    assert set(panel.loc[panel.event_week.between(1, 16), "period"]) == {"post"}
    assert set(panel["causal_status"]) == {CAUSAL_STATUS}


def test_diagnostics_recover_known_gap_shift_and_zero_pretrend():
    config = load_gnosis_donor_config(CONFIG_PATH)
    calendar = read_common_calendar(config)
    panel = pd.DataFrame(
        build_common_panel(
            config,
            _synthetic_weekly(),
            _synthetic_weekly(chain_offset=5, post_effect=2),
            calendar,
        )
    )
    result = estimate_did(
        config,
        panel,
        "log_active_beneficiary_addresses",
        post_horizon=16,
    )
    expected = np.mean(
        [
            np.log1p(100 + week + 7) - np.log1p(100 + week)
            for week in range(1, 17)
        ]
    ) - np.mean(
        [
            np.log1p(100 + week + 5) - np.log1p(100 + week)
            for week in range(-16, -8)
        ]
    )
    assert np.isclose(result["difference_in_changes_arbitrum_minus_gnosis"], expected)
    hhi = estimate_did(config, panel, "beneficiary_hhi", post_horizon=16)
    assert np.isclose(hhi["difference_in_changes_arbitrum_minus_gnosis"], 0.02)
    pretrend = pretrend_diagnostic(config, panel, "beneficiary_hhi")
    assert np.isclose(pretrend["gap_slope_per_event_week"], 0.0)
    assert all(
        np.isclose(row["gap_change"], 0.0)
        for row in placebo_diagnostics(config, panel, "beneficiary_hhi")
    )
    assert result["causal_status"] == CAUSAL_STATUS


def test_hac_uses_actual_calendar_spacing_across_excluded_weeks():
    y = np.array([0.0, 1.0, -0.5, 2.0])
    x = np.column_stack([np.ones(4), np.array([0.0, 0.0, 1.0, 1.0])])
    compressed = newey_west_ols(y, x, lag=1)
    calendar = newey_west_ols(
        y,
        x,
        lag=1,
        time_index=np.array([-10, -9, 1, 2]),
    )
    # No HAC cross term should link week -9 to week +1.
    assert not np.allclose(compressed["standard_errors"], calendar["standard_errors"])


def test_primary_calendar_aware_hac_regression_values():
    config = load_gnosis_donor_config(CONFIG_PATH)
    panel = pd.read_csv("outputs/real_v6/arbitrum_gnosis_did_mvp/common_calendar_panel.csv")
    result = estimate_did(
        config,
        panel,
        "log_active_beneficiary_addresses",
        post_horizon=16,
    )
    assert result["nw_standard_error"] == pytest.approx(0.22678, abs=5e-5)
    assert result["ci_lower"] == pytest.approx(1.5388, abs=2e-4)
    assert result["ci_upper"] == pytest.approx(2.4278, abs=2e-4)


def test_event_study_figure_is_editable_vector(tmp_path):
    rows = []
    for outcome in ("log_active_beneficiary_addresses", "beneficiary_hhi"):
        for week in range(-16, 17):
            rows.append(
                {
                    "outcome_id": outcome,
                    "event_week": week,
                    "period": "clean_pre" if week <= -9 else "post",
                    "gap_relative_to_clean_pre_mean": week / 100,
                }
            )
    path = render_event_study_svg(rows, tmp_path / "event_study.svg")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<svg")
    assert "diagnostic, not causal" in text
    assert "<polyline" in text
