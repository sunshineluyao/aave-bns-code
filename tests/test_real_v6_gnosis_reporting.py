import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_real_v6_gnosis_paper_assets.py"
SPEC = importlib.util.spec_from_file_location("real_v6_reporting", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _rows(name: str) -> list[dict[str, str]]:
    path = ROOT / "outputs/real_v6/arbitrum_gnosis_did_mvp" / name
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_frozen_primary_numbers_and_claim_gate_are_exact():
    estimates = _rows("estimates.csv")
    participation = MODULE.by_outcome_horizon(
        estimates, "log_active_beneficiary_addresses", 16
    )
    concentration = MODULE.by_outcome_horizon(estimates, "beneficiary_hhi", 16)
    assert float(participation["arbitrum_change"]) == pytest.approx(0.0314066693112)
    assert float(participation["gnosis_change"]) == pytest.approx(-1.95188861226)
    assert float(
        participation["difference_in_changes_arbitrum_minus_gnosis"]
    ) == pytest.approx(1.98329528157)
    assert float(concentration["arbitrum_change"]) == pytest.approx(0.00501170707165)
    assert float(concentration["gnosis_change"]) == pytest.approx(0.0234321217772)
    assert float(
        concentration["difference_in_changes_arbitrum_minus_gnosis"]
    ) == pytest.approx(-0.0184204147055)
    summary = json.loads(
        (ROOT / "outputs/real_v6/arbitrum_gnosis_did_mvp/summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["causal_estimate_produced"] is False
    assert summary["causal_language_permitted"] is False


def test_reporting_inputs_cover_the_locked_common_calendar():
    panel = _rows("common_calendar_panel.csv")
    estimates = _rows("estimates.csv")
    summary = json.loads(
        (ROOT / "outputs/real_v6/arbitrum_gnosis_did_mvp/summary.json").read_text(
            encoding="utf-8"
        )
    )
    MODULE.validate_inputs(panel, estimates, summary)


def test_generated_table_and_figure_preserve_interpretation_boundary(tmp_path):
    estimates = _rows("estimates.csv")
    panel = _rows("common_calendar_panel.csv")
    table = MODULE.render_main_table(estimates)
    figure = MODULE.render_figure(panel)
    assert "+1.9833" in table
    assert "-0.01842" in table
    assert "not a confidence interval for a treatment effect" in table
    assert "HHI rose less on Arbitrum" in table
    assert "failed-donor diagnostic, not a causal" in figure
    assert "A. Log participation" in figure
    assert "B. Pool-event-frequency HHI" in figure
    assert "\\includegraphics" not in figure
