from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_pilot_summary_is_reproducible_and_fails_closed():
    summary = json.loads(
        (ROOT / "outputs/real_v5/pilot_did/summary.json").read_text(encoding="utf-8")
    )
    input_path = ROOT / summary["input"]
    assert summary["input_sha256"] == sha256(input_path)
    assert summary["pilot_interaction_estimate_produced"] is True
    assert summary["causal_estimate_produced"] is False
    assert summary["causal_language_permitted"] is False
    assert len(summary["identification_failures"]) == 4
    assert "different calendar periods" in summary["identification_failures"][0]


def test_primary_and_window_sensitivity_values_match_the_locked_panel():
    estimates = pd.read_csv(ROOT / "outputs/real_v5/pilot_did/estimates.csv")
    assert set(estimates["causal_status"]) == {"diagnostic_not_causal"}
    log_rows = estimates[
        estimates["outcome_id"] == "log_active_beneficiary_addresses"
    ].set_index("window")
    hhi_rows = estimates[estimates["outcome_id"] == "beneficiary_hhi"].set_index(
        "window"
    )
    assert abs(
        log_rows.loc[16, "difference_in_changes_arbitrum_minus_ethereum"]
        - (-0.6505363754364986)
    ) < 1e-12
    assert abs(
        hhi_rows.loc[16, "difference_in_changes_arbitrum_minus_ethereum"]
        - 0.008429464155833732
    ) < 1e-12
    assert abs(
        log_rows.loc[8, "difference_in_changes_arbitrum_minus_ethereum"]
    ) < abs(log_rows.loc[16, "difference_in_changes_arbitrum_minus_ethereum"])


def test_manuscript_labels_the_pilot_as_noncausal():
    section = (ROOT / "paper/sections/05_causal_inference.tex").read_text(
        encoding="utf-8"
    )
    table = (ROOT / "paper/tables/tab06_pilot_did.tex").read_text(encoding="utf-8")
    assert "DiD-form pilot: a diagnostic, not an effect" in section
    assert "it is not an identified DiD effect" in section
    assert "No entry is a treatment effect" in table
