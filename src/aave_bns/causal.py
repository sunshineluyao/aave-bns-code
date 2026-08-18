from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class DifferenceInDifferencesResult:
    outcome: str
    treated_pre: float
    treated_post: float
    control_pre: float
    control_post: float
    estimate: float
    n_observations: int
    synthetic_demo: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def two_by_two_did(
    frame: pd.DataFrame,
    *,
    outcome: str,
    treated: str = "treated",
    post: str = "post",
) -> DifferenceInDifferencesResult:
    required = {outcome, treated, post}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing causal columns: {sorted(missing)}")
    data = frame.copy()
    data[treated] = data[treated].astype(int)
    data[post] = data[post].astype(int)
    means = data.groupby([treated, post], observed=True)[outcome].mean()
    cells = {}
    for t in (0, 1):
        for p in (0, 1):
            value = means.get((t, p), math.nan)
            if pd.isna(value):
                raise ValueError(f"DID cell treated={t}, post={p} is empty")
            cells[(t, p)] = float(value)
    estimate = (cells[(1, 1)] - cells[(1, 0)]) - (cells[(0, 1)] - cells[(0, 0)])
    return DifferenceInDifferencesResult(
        outcome=outcome,
        treated_pre=cells[(1, 0)],
        treated_post=cells[(1, 1)],
        control_pre=cells[(0, 0)],
        control_post=cells[(0, 1)],
        estimate=float(estimate),
        n_observations=len(data),
    )
