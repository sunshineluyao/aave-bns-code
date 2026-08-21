from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_model_based_inference.py"
SPEC = importlib.util.spec_from_file_location("model_based_inference", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_generates_complete_stable_ledger(tmp_path: Path) -> None:
    weeks = np.arange(-16, 17)
    chain_rows = []
    for chain, offset in (("Ethereum", 0.0), ("Arbitrum", 0.4)):
        for week in weeks:
            chain_rows.append(
                {
                    "chain": chain,
                    "event_week": week,
                    "active_beneficiary_addresses": 1000 + 8 * week + (70 if week > 0 else 0) + 50 * offset,
                    "beneficiary_hhi": 0.02 + 0.0002 * week - (0.001 if week > 0 else 0) + 0.002 * offset,
                }
            )
    chain = pd.DataFrame(chain_rows)
    event_rows = []
    for outcome, scale in (("log_active_beneficiary_addresses", 1.0), ("beneficiary_hhi", 0.01)):
        for week in weeks:
            gap = scale * (0.1 * week + (0.8 + 0.03 * week if week > 0 else 0))
            event_rows.append({"outcome_id": outcome, "event_week": week, "gap_arbitrum_minus_gnosis": gap})
    event = pd.DataFrame(event_rows)
    chain_path = tmp_path / "chain.csv"
    event_path = tmp_path / "event.csv"
    chain.to_csv(chain_path, index=False)
    event.to_csv(event_path, index=False)

    ledger = MODULE.generate(chain_path, event_path)

    assert len(ledger) == 42
    assert set(ledger["analysis_family"]) == {
        "Chain-relative pre/post",
        "Primary DiD-style comparison",
        "Primary controlled ITS",
        "Clean-pre path diagnostic",
        "Post-horizon sensitivity",
        "Anticipation-window sensitivity",
    }
    assert not (ledger["two_sided_p_value"].dropna() == 0).any()
    assert (ledger["finite_sample_correction"] == "n/(n-k)").all()
    assert ledger["calendar_aware"].all()
