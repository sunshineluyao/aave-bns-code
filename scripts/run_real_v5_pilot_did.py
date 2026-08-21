#!/usr/bin/env python3
"""Run the two-chain event-aligned DiD-form diagnostic.

The current compact release contains one Ethereum issuance cohort and one
Arbitrum cross-chain cohort. Their calendar windows do not overlap and both
units receive different treatments. The interaction below is therefore a
difference-in-changes pipeline pilot, not an identified treatment effect.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "outputs/real_v5/descriptive/weekly_address_metrics.csv"
OUTPUT_DIR = ROOT / "outputs/real_v5/pilot_did"
TABLE_PATH = ROOT / "outputs/real_v5/pilot_did/pilot_did_table.tex"

CHAINS = ("Ethereum", "Arbitrum")
WINDOWS = (16, 12, 8)
PRIMARY_WINDOW = 16
NW_LAG = 4
SERIALIZATION_SIGNIFICANT_DIGITS = 12
PVALUE_SIGNIFICANT_DIGITS = 10
OUTCOMES = {
    "log_active_beneficiary_addresses": {
        "label": r"\shortstack[l]{$\log(1+N_{\mathrm{active}})$\\beneficiary addresses}",
        "column": "log_active_beneficiary_addresses",
        "digits": 3,
    },
    "beneficiary_hhi": {
        "label": "Beneficiary-event HHI",
        "column": "beneficiary_hhi",
        "digits": 5,
    },
}


def stable_float(
    value: float,
    significant_digits: int = SERIALIZATION_SIGNIFICANT_DIGITS,
) -> float:
    """Remove platform-specific linear-algebra noise before serialization."""
    value = float(value)
    if not math.isfinite(value):
        return value
    return float(f"{value:.{significant_digits}g}")


def stabilize_row(row: dict[str, object]) -> dict[str, object]:
    return {
        key: stable_float(value) if isinstance(value, float | np.floating) else value
        for key, value in row.items()
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_panel() -> pd.DataFrame:
    panel = pd.read_csv(INPUT_PATH)
    required = {
        "chain",
        "event_week",
        "active_beneficiary_addresses",
        "beneficiary_hhi",
        "causal_status",
    }
    missing = sorted(required.difference(panel.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if set(panel["chain"]) != set(CHAINS):
        raise ValueError("Pilot requires exactly the Ethereum and Arbitrum checkpoints")
    if set(panel["causal_status"]) != {"descriptive_only"}:
        raise ValueError("Pilot inputs must remain marked descriptive_only")
    expected_weeks = list(range(-16, 17))
    for chain in CHAINS:
        chain_weeks = sorted(panel.loc[panel["chain"] == chain, "event_week"].tolist())
        if chain_weeks != expected_weeks:
            raise ValueError(f"{chain} must contain one row for every event week -16 to +16")
    panel = panel.copy()
    panel["log_active_beneficiary_addresses"] = np.log1p(
        panel["active_beneficiary_addresses"].astype(float)
    )
    return panel


def newey_west_ols(
    y: np.ndarray,
    x: np.ndarray,
    *,
    lag: int,
) -> dict[str, np.ndarray]:
    """OLS with a Bartlett-kernel Newey--West diagnostic covariance."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n_obs, n_params = x.shape
    xtx_inverse = np.linalg.pinv(x.T @ x)
    coefficients = xtx_inverse @ (x.T @ y)
    residuals = y - x @ coefficients

    meat = np.zeros((n_params, n_params), dtype=float)
    for index in range(n_obs):
        meat += residuals[index] ** 2 * np.outer(x[index], x[index])
    for offset in range(1, min(lag, n_obs - 1) + 1):
        weight = 1.0 - offset / (lag + 1.0)
        for index in range(offset, n_obs):
            cross = np.outer(x[index], x[index - offset])
            meat += weight * residuals[index] * residuals[index - offset] * (
                cross + cross.T
            )
    covariance = xtx_inverse @ meat @ xtx_inverse
    if n_obs > n_params:
        covariance *= n_obs / (n_obs - n_params)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    return {
        "coefficients": coefficients,
        "standard_errors": standard_errors,
        "covariance": covariance,
    }


def normal_diagnostic(estimate: float, standard_error: float) -> dict[str, float]:
    if standard_error <= 0:
        return {
            "ci_lower": estimate,
            "ci_upper": estimate,
            "normal_approx_p_value": math.nan,
        }
    z_value = estimate / standard_error
    p_value = 2.0 * (1.0 - NormalDist().cdf(abs(z_value)))
    return {
        "ci_lower": estimate - 1.96 * standard_error,
        "ci_upper": estimate + 1.96 * standard_error,
        "normal_approx_p_value": stable_float(p_value, PVALUE_SIGNIFICANT_DIGITS),
    }


def outcome_pivot(panel: pd.DataFrame, outcome: str) -> pd.DataFrame:
    pivot = panel.pivot(index="event_week", columns="chain", values=outcome).sort_index()
    if list(pivot.columns) != ["Arbitrum", "Ethereum"]:
        pivot = pivot[["Arbitrum", "Ethereum"]]
    return pivot


def estimate_window(panel: pd.DataFrame, outcome: str, window: int) -> dict[str, object]:
    pivot = outcome_pivot(panel, outcome)
    pivot = pivot[(pivot.index != 0) & (pivot.index.to_series().abs() <= window)]
    pre = pivot.index < 0
    post = pivot.index > 0
    means = {
        chain: {
            "pre": float(pivot.loc[pre, chain].mean()),
            "post": float(pivot.loc[post, chain].mean()),
        }
        for chain in CHAINS
    }
    changes = {
        chain: means[chain]["post"] - means[chain]["pre"]
        for chain in CHAINS
    }

    gap = (pivot["Arbitrum"] - pivot["Ethereum"]).to_numpy(dtype=float)
    design = np.column_stack(
        [np.ones(len(pivot), dtype=float), post.astype(float)]
    )
    fit = newey_west_ols(gap, design, lag=NW_LAG)
    estimate = float(fit["coefficients"][1])
    standard_error = float(fit["standard_errors"][1])
    inference = normal_diagnostic(estimate, standard_error)
    return stabilize_row({
        "outcome_id": outcome,
        "window": window,
        "event_week_zero_excluded": True,
        "ethereum_pre_mean": means["Ethereum"]["pre"],
        "ethereum_post_mean": means["Ethereum"]["post"],
        "ethereum_change": changes["Ethereum"],
        "arbitrum_pre_mean": means["Arbitrum"]["pre"],
        "arbitrum_post_mean": means["Arbitrum"]["post"],
        "arbitrum_change": changes["Arbitrum"],
        "difference_in_changes_arbitrum_minus_ethereum": estimate,
        "nw_lag": NW_LAG,
        "nw_standard_error": standard_error,
        **inference,
        "causal_status": "diagnostic_not_causal",
    })


def pretrend_diagnostic(panel: pd.DataFrame, outcome: str) -> dict[str, object]:
    pivot = outcome_pivot(panel, outcome)
    pivot = pivot[pivot.index < 0]
    gap = (pivot["Arbitrum"] - pivot["Ethereum"]).to_numpy(dtype=float)
    design = np.column_stack(
        [np.ones(len(pivot), dtype=float), pivot.index.to_numpy(dtype=float)]
    )
    fit = newey_west_ols(gap, design, lag=NW_LAG)
    slope = float(fit["coefficients"][1])
    standard_error = float(fit["standard_errors"][1])
    inference = normal_diagnostic(slope, standard_error)
    return stabilize_row({
        "outcome_id": outcome,
        "pre_event_weeks": "-16:-1",
        "gap_slope_per_event_week": slope,
        "nw_lag": NW_LAG,
        "nw_standard_error": standard_error,
        **inference,
        "interpretation": "low_power_diagnostic_only",
    })


def placebo_diagnostics(panel: pd.DataFrame, outcome: str) -> list[dict[str, object]]:
    pivot = outcome_pivot(panel, outcome)
    gap = (pivot["Arbitrum"] - pivot["Ethereum"])[pivot.index < 0]
    rows: list[dict[str, object]] = []
    half_window = 4
    for pseudo_week in (-12, -10, -8, -6):
        before = gap.loc[pseudo_week - half_window : pseudo_week - 1]
        after = gap.loc[pseudo_week + 1 : pseudo_week + half_window]
        if len(before) != half_window or len(after) != half_window:
            raise ValueError(f"Incomplete placebo support around event week {pseudo_week}")
        rows.append(
            stabilize_row({
                "outcome_id": outcome,
                "pseudo_event_week": pseudo_week,
                "half_window": half_window,
                "difference_in_changes_arbitrum_minus_ethereum": float(
                    after.mean() - before.mean()
                ),
                "causal_status": "preperiod_placebo_diagnostic",
            })
        )
    return rows


def format_value(value: float, digits: int, *, signed: bool = True) -> str:
    specifier = f"+.{digits}f" if signed else f".{digits}f"
    return format(value, specifier)


def render_table(estimates: list[dict[str, object]]) -> str:
    primary = {
        row["outcome_id"]: row
        for row in estimates
        if int(row["window"]) == PRIMARY_WINDOW
    }
    table_rows = []
    for outcome_id, specification in OUTCOMES.items():
        row = primary[outcome_id]
        digits = int(specification["digits"])
        interval = (
            f"[{format_value(float(row['ci_lower']), digits)}, "
            f"{format_value(float(row['ci_upper']), digits)}]"
        )
        table_rows.append(
            " & ".join(
                [
                    str(specification["label"]),
                    format_value(float(row["ethereum_change"]), digits),
                    format_value(float(row["arbitrum_change"]), digits),
                    format_value(
                        float(row["difference_in_changes_arbitrum_minus_ethereum"]),
                        digits,
                    ),
                    interval,
                ]
            )
            + r" \\"
        )
    body = "\n".join(table_rows)
    return rf"""% Generated by scripts/run_real_v5_pilot_did.py; do not edit by hand.
\begin{{table}}[htbp]
\centering
\caption{{Two-chain event-aligned difference-in-changes pilot (diagnostic, not causal).}}
\label{{tab:pilot-did}}
\small
\AVTableSetup
\begin{{tabularx}}{{0.98\linewidth}}{{Xrrrr}}
\toprule
\AVTableHeader
\textbf{{Outcome}} & \textbf{{Ethereum}} & \textbf{{Arbitrum}} &
\textbf{{Arb. $-$ Eth.}} & \textbf{{NW(4) interval}} \\
\midrule
{body}
\bottomrule
\end{{tabularx}}
\begin{{tablenotes}}[flushleft]
\footnotesize
\item \emph{{Notes:}} For the first outcome, $N_{{\mathrm{{active}}}}$ is the number of
active beneficiary addresses. Entries compare mean outcome changes from event weeks $-16$ to $-1$
and $+1$ to $+16$, omitting week zero. Each chain is aligned to its own activation in a
different calendar period, and both chains receive different protocol shocks. The
interaction is Arbitrum minus Ethereum. Newey--West lag-4 intervals describe serial
variation in the paired event-week gap only; with two treated units they do not provide
valid chain-cluster causal inference. No entry is a treatment effect.
\end{{tablenotes}}
\end{{table}}
"""


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")


def main() -> None:
    panel = load_panel()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)

    estimates = [
        estimate_window(panel, outcome, window)
        for outcome in OUTCOMES
        for window in WINDOWS
    ]
    pretrends = [pretrend_diagnostic(panel, outcome) for outcome in OUTCOMES]
    placebos = [
        row
        for outcome in OUTCOMES
        for row in placebo_diagnostics(panel, outcome)
    ]

    write_csv(OUTPUT_DIR / "estimates.csv", estimates)
    write_csv(OUTPUT_DIR / "pretrend_diagnostics.csv", pretrends)
    write_csv(OUTPUT_DIR / "preperiod_placebos.csv", placebos)
    TABLE_PATH.write_text(render_table(estimates), encoding="utf-8")

    primary = {
        row["outcome_id"]: row
        for row in estimates
        if int(row["window"]) == PRIMARY_WINDOW
    }
    summary = {
        "schema_version": 1,
        "status": "two_chain_event_aligned_pipeline_pilot",
        "input": str(INPUT_PATH.relative_to(ROOT)),
        "input_sha256": sha256(INPUT_PATH),
        "chains": list(CHAINS),
        "event_week_range": [-16, 16],
        "primary_window": [-16, 16],
        "event_week_zero_excluded": True,
        "contrast": "Arbitrum cross-chain activation minus Ethereum issuance",
        "pilot_interaction_estimate_produced": True,
        "causal_estimate_produced": False,
        "causal_language_permitted": False,
        "identification_failures": [
            "the chain windows occupy different calendar periods",
            "both observed chains receive treatment",
            "the two treatments are economically distinct",
            "two chain units cannot support chain-cluster causal inference",
        ],
        "primary_results": primary,
        "pretrend_diagnostics": {row["outcome_id"]: row for row in pretrends},
        "preperiod_placebo_max_abs": {
            outcome: max(
                abs(float(row["difference_in_changes_arbitrum_minus_ethereum"]))
                for row in placebos
                if row["outcome_id"] == outcome
            )
            for outcome in OUTCOMES
        },
        "interpretation": (
            "This output validates the final stacked-DiD pipeline and reports an observed "
            "difference in lifecycle changes. It is not an identified treatment effect."
        ),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
