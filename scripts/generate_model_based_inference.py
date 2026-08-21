#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path
import numpy as np
import pandas as pd

COLS = [
    "analysis_family",
    "outcome",
    "term",
    "specification",
    "estimate",
    "hac_standard_error",
    "test_statistic_type",
    "test_statistic",
    "degrees_of_freedom",
    "two_sided_p_value",
    "ci_level",
    "ci_low_model_based",
    "ci_high_model_based",
    "n",
    "hac_lag",
    "calendar_aware",
    "finite_sample_correction",
    "interpretation_status",
]
LABELS = {
    "log_active_beneficiary_addresses": "Log participation",
    "beneficiary_hhi": "Position-holder-event HHI",
}


def ols_hac(y, x, lag, weeks):
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    weeks = np.asarray(weeks, int)
    n, k = x.shape
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    u = y - x @ beta
    xu = x * u[:, None]
    meat = xu.T @ xu
    for i in range(n):
        for j in range(i):
            d = int(weeks[i] - weeks[j])
            if 1 <= d <= lag:
                w = 1 - d / (lag + 1)
                cross = np.outer(xu[i], xu[j])
                meat += w * (cross + cross.T)
    bread = np.linalg.inv(x.T @ x)
    cov = bread @ meat @ bread * (n / (n - k))
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    return beta, se, cov


def inference(est, se):
    z = est / se
    return z, math.erfc(abs(z) / math.sqrt(2)), est - 1.96 * se, est + 1.96 * se


def row(family, outcome, term, est, se, n, lag, status, spec="primary"):
    z, p, lo, hi = inference(float(est), float(se))
    return dict(
        zip(
            COLS,
            [
                family,
                outcome,
                term,
                spec,
                est,
                se,
                "z_normal_approximation",
                z,
                np.nan,
                p,
                0.95,
                lo,
                hi,
                n,
                lag,
                True,
                "n/(n-k)",
                status,
            ],
        )
    )


def wald_row(outcome, stat, p, n, lag):
    return dict(
        zip(
            COLS,
            [
                "Primary controlled ITS",
                outcome,
                "Joint level-and-slope shift",
                "primary",
                np.nan,
                np.nan,
                "robust_wald_chi_square",
                stat,
                2,
                p,
                np.nan,
                np.nan,
                np.nan,
                n,
                lag,
                True,
                "n/(n-k)",
                "working_model_joint_test_not_identification_test",
            ],
        )
    )


def fit_did(gap, pre, post, lag=3):
    weeks = np.array(pre + post)
    x = np.c_[np.ones(len(weeks)), weeks >= 1]
    b, se, c = ols_hac(gap.loc[weeks], x, lag, weeks)
    return b[1], se[1], len(weeks)


def fit_its(gap, pre, post, lag=2):
    weeks = np.array(pre + post)
    post_i = (weeks >= 1).astype(float)
    x = np.c_[np.ones(len(weeks)), weeks, post_i, weeks * post_i]
    b, se, c = ols_hac(gap.loc[weeks], x, lag, weeks)
    q = b[[2, 3]]
    qc = c[np.ix_([2, 3], [2, 3])]
    stat = float(q @ np.linalg.pinv(qc) @ q)
    return b, se, stat, math.exp(-stat / 2), len(weeks)


def fit_pretrend(gap):
    weeks = np.arange(-16, -8)
    x = np.c_[np.ones(8), weeks]
    b, se, c = ols_hac(gap.loc[weeks], x, 2, weeks)
    return b[1], se[1]


def gaps_from_long(path):
    d = pd.read_csv(path)
    out = {}
    for oid, g in d.groupby("outcome_id"):
        g = g.sort_values("event_week")
        out[oid] = pd.Series(
            g.gap_arbitrum_minus_gnosis.values, index=g.event_week.astype(int)
        )
    return out


def generate(chain_path, event_path):
    rows = []
    chain = pd.read_csv(chain_path)
    for name in ["Ethereum", "Arbitrum"]:
        f = chain[(chain.chain == name) & (chain.event_week != 0)].sort_values(
            "event_week"
        )
        weeks = f.event_week.to_numpy(int)
        x = np.c_[np.ones(len(weeks)), weeks >= 1]
        for col, label in [
            ("active_beneficiary_addresses", "Active position-holder addresses"),
            ("beneficiary_hhi", "Position-holder-event HHI"),
        ]:
            b, se, c = ols_hac(f[col], x, 3, weeks)
            rows.append(
                row(
                    "Chain-relative pre/post",
                    f"{name}: {label}",
                    "Post-minus-pre weekly mean",
                    b[1],
                    se[1],
                    len(weeks),
                    3,
                    "descriptive_chain_relative_not_event_effect",
                )
            )
    gaps = gaps_from_long(event_path)
    primary_pre = list(range(-16, -8))
    primary_post = list(range(1, 17))
    for oid in ["log_active_beneficiary_addresses", "beneficiary_hhi"]:
        gap = gaps[oid]
        label = LABELS[oid]
        est, se, n = fit_did(gap, primary_pre, primary_post)
        rows.append(
            row(
                "Primary DiD-style comparison",
                label,
                "Arbitrum-minus-Gnosis change",
                est,
                se,
                n,
                3,
                "comparative_longitudinal_not_identified_effect",
            )
        )
        b, ses, stat, p, n = fit_its(gap, primary_pre, primary_post)
        terms = [
            "cITS intercept",
            "cITS pre-gap slope/week",
            "cITS level shift at week 0",
            "cITS slope shift/week",
        ]
        for i, t in enumerate(terms):
            rows.append(
                row(
                    "Primary controlled ITS",
                    label,
                    t,
                    b[i],
                    ses[i],
                    n,
                    2,
                    "comparative_longitudinal_not_identified_effect",
                )
            )
        rows.append(wald_row(label, stat, p, n, 2))
        est, se = fit_pretrend(gap)
        rows.append(
            row(
                "Clean-pre path diagnostic",
                label,
                "Standalone gap slope/week",
                est,
                se,
                8,
                2,
                "comparison_support_diagnostic_not_parallel_trends_validation",
            )
        )
    for horizon in [8, 12, 16]:
        post = list(range(1, horizon + 1))
        for oid in ["log_active_beneficiary_addresses", "beneficiary_hhi"]:
            est, se, n = fit_did(gaps[oid], primary_pre, post)
            rows.append(
                row(
                    "Post-horizon sensitivity",
                    LABELS[oid],
                    f"DiD-style change through week +{horizon}",
                    est,
                    se,
                    n,
                    3,
                    "comparative_longitudinal_sensitivity",
                    f"post_horizon_{horizon}",
                )
            )
    for excluded in [0, 4, 8]:
        pre = list(range(-16, -excluded if excluded else 0))
        spec = f"anticipation_exclusion_{excluded}_weeks"
        for oid in ["log_active_beneficiary_addresses", "beneficiary_hhi"]:
            gap = gaps[oid]
            label = LABELS[oid]
            est, se, n = fit_did(gap, pre, primary_post)
            rows.append(
                row(
                    "Anticipation-window sensitivity",
                    label,
                    f"DiD-style change ({excluded}-week exclusion)",
                    est,
                    se,
                    n,
                    3,
                    "comparative_longitudinal_sensitivity",
                    spec,
                )
            )
            b, ses, stat, p, n = fit_its(gap, pre, primary_post)
            rows.append(
                row(
                    "Anticipation-window sensitivity",
                    label,
                    f"cITS level shift ({excluded}-week exclusion)",
                    b[2],
                    ses[2],
                    n,
                    2,
                    "comparative_longitudinal_sensitivity",
                    spec,
                )
            )
            rows.append(
                row(
                    "Anticipation-window sensitivity",
                    label,
                    f"cITS slope shift ({excluded}-week exclusion)",
                    b[3],
                    ses[3],
                    n,
                    2,
                    "comparative_longitudinal_sensitivity",
                    spec,
                )
            )
    out = pd.DataFrame(rows, columns=COLS)
    if len(out) != 42 or (out.two_sided_p_value == 0).any():
        raise RuntimeError("inference ledger gate failed")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--chain-input", type=Path, required=True)
    p.add_argument("--event-study-input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    out = generate(a.chain_input, a.event_study_input)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.output, index=False, lineterminator="\n", float_format="%.15g")
    print(f"wrote {len(out)} rows to {a.output}")


if __name__ == "__main__":
    main()
