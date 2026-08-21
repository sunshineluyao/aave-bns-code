from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from .aave_v3_events import decode_pool_log, event_topics
from .config import load_yaml
from .evm_rpc import RpcStats
from .provenance import sha256_file, write_manifest
from .real_v2 import parse_utc
from .real_v2_ethereum import (
    _write_processed_events,
    assign_event_weeks,
    boundary_targets,
    build_chunks,
    fetch_log_chunks,
    gzip_payload_sha256,
    project_relative_path,
    read_cohort_calendar,
    resolve_boundaries,
    safe_rpc_endpoint,
    source_revision,
    write_csv_records,
)
from .real_v5_arbitrum import (
    _canonical_json_list_sha256,
    _PacedLogClient,
    _RedactingRpcClient,
    build_weekly_beneficiary_panel,
    cross_provider_consensus_checks,
)

CONFIG_PATH = "configs/real_v6_gnosis_donor.yaml"
CAUSAL_STATUS = "diagnostic_not_causal"
SERIALIZATION_SIGNIFICANT_DIGITS = 12
PVALUE_SIGNIFICANT_DIGITS = 10


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    if not rows:
        raise ValueError("Cannot write an empty table")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")
    return path


def _validated_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be a complete HTTP(S) URL")
    return value


def _required_environment_url(variable: str) -> str:
    value = os.getenv(variable, "").strip()
    if not value:
        raise ValueError(f"Required GitHub Actions secret {variable} is not available")
    return _validated_url(value, label=variable)


def load_gnosis_donor_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    config = load_yaml(path)
    if not isinstance(config, dict) or int(config.get("schema_version", 0)) != 1:
        raise ValueError("Gnosis donor configuration requires schema_version 1")
    design = config["design"]
    donor = config["donor"]
    if bool(design["causal_estimates_allowed"]):
        raise ValueError("The two-chain MVP cannot authorize causal estimates")
    if bool(config["release_gate"]["causal_estimates_allowed"]):
        raise ValueError("The release gate cannot authorize causal estimates")
    if int(design["treated_chain_id"]) != 42161 or int(donor["chain_id"]) != 100:
        raise ValueError("The MVP is locked to treated Arbitrum and donor Gnosis")
    if str(design["treated_cohort_id"]) != "arbitrum_gho":
        raise ValueError("The treatment calendar must be the locked Arbitrum GHO cohort")
    minimum = int(design["minimum_event_week"])
    maximum = int(design["maximum_event_week"])
    if (minimum, maximum) != (-16, 16):
        raise ValueError("The common calendar must retain event weeks [-16,+16]")
    included = (
        list(
            range(
                int(design["clean_pre_minimum_event_week"]),
                int(design["clean_pre_maximum_event_week"]) + 1,
            )
        )
        + list(
            range(
                int(design["anticipation_minimum_event_week"]),
                int(design["anticipation_maximum_event_week"]) + 1,
            )
        )
        + [0]
        + list(
            range(
                int(design["post_minimum_event_week"]),
                int(design["post_maximum_event_week"]) + 1,
            )
        )
    )
    if included != list(range(minimum, maximum + 1)):
        raise ValueError("Pre, anticipation, week-zero, and post periods must partition the panel")
    if not bool(design["event_week_zero_excluded"]):
        raise ValueError("Week zero must remain excluded from the primary contrast")
    providers = config["providers"]
    if not str(providers["primary"]["environment_variable"]).endswith("_RPC_URL"):
        raise ValueError("The primary provider must be supplied through a URL secret")
    endpoints = [
        _validated_url(str(providers[role]["url"]), label=f"{role} RPC")
        for role in ("bulk", "log_crosscheck")
    ]
    if len({safe_rpc_endpoint(value) for value in endpoints}) != len(endpoints):
        raise ValueError("Bulk and log cross-check endpoints must be independent")
    retrieval = config["retrieval"]
    cache_width = int(retrieval["maximum_blocks_per_cache_chunk"])
    query_width = int(retrieval["blocks_per_log_query"])
    minimum_width = int(retrieval["minimum_adaptive_blocks_per_log_query"])
    if not 1 <= minimum_width <= query_width <= cache_width:
        raise ValueError("Log widths must satisfy 1 <= adaptive <= query <= cache")
    if int(retrieval["maximum_runtime_seconds"]) <= 0:
        raise ValueError("The resumable retrieval time slice must be positive")
    return config


def read_common_calendar(
    config: dict[str, Any], root: str | Path = "."
) -> list[dict[str, Any]]:
    project = Path(root)
    design = config["design"]
    donor = config["donor"]
    calendar = read_cohort_calendar(
        project / design["calendar"],
        cohort_id=str(design["treated_cohort_id"]),
        minimum_event_week=int(design["minimum_event_week"]),
        maximum_event_week=int(design["maximum_event_week"]),
    )
    if {row["activation_utc"] for row in calendar} != {str(design["treatment_utc"])}:
        raise ValueError("The calendar treatment timestamp differs from the locked design")
    first = parse_utc(str(calendar[0]["window_start_utc"]))
    last = parse_utc(str(calendar[-1]["window_end_utc_exclusive"]))
    if parse_utc(str(donor["market_available_by_utc"])) > first:
        raise ValueError("The Gnosis Aave market was not active for the full donor window")
    if parse_utc(str(donor["own_anticipation_utc"])) < last:
        raise ValueError("The Gnosis donor window overlaps its own public anticipation")
    rows = []
    for raw in calendar:
        row = dict(raw)
        row["calendar_cohort_id"] = row.pop("cohort_id")
        row["calendar_chain"] = row.pop("chain")
        row["calendar_chain_id"] = row.pop("chain_id")
        row["cohort_id"] = donor["cohort_id"]
        row["chain"] = donor["chain"]
        row["chain_id"] = donor["chain_id"]
        rows.append(row)
    return rows


def read_donor_boundary_cache(
    path: str | Path,
    targets: list[tuple[int, datetime]],
) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    expected = [
        (week, target.isoformat().replace("+00:00", "Z")) for week, target in targets
    ]
    observed = [(int(row["boundary_event_week"]), row["target_utc"]) for row in records]
    if observed != expected:
        raise ValueError("Cached Gnosis boundaries do not match the common calendar")
    blocks = [int(row["start_block"]) for row in records]
    if blocks != sorted(set(blocks)):
        raise ValueError("Cached Gnosis boundary blocks are not strictly increasing")
    for row in records:
        target = parse_utc(str(row["target_utc"]))
        selected = parse_utc(str(row["start_block_timestamp"]))
        previous = parse_utc(str(row["previous_block_timestamp"]))
        if selected < target or previous >= target:
            raise ValueError("Cached boundary does not prove the adjacent-header rule")
        if int(row["lag_seconds"]) != int((selected - target).total_seconds()):
            raise ValueError("Cached boundary lag is inconsistent")
        block_hash = str(row["start_block_hash"])
        if not block_hash.startswith("0x") or len(block_hash) != 66:
            raise ValueError("Cached boundary has a malformed block hash")
    return records


def _provider_fingerprint(client: Any, *, pool_address: str, block_number: int) -> dict[str, Any]:
    block = client.block(block_number)
    code = client.code(pool_address, block_number)
    return {
        "chain_id": int(client.chain_id()),
        "block_number": block_number,
        "block_hash": str(block["hash"]).lower(),
        "block_timestamp": int(str(block["timestamp"]), 16),
        "pool_code_present": code != "0x",
        "pool_code_sha256": hashlib.sha256(code.encode()).hexdigest(),
    }


def _period(config: dict[str, Any], event_week: int) -> str:
    design = config["design"]
    if int(design["clean_pre_minimum_event_week"]) <= event_week <= int(
        design["clean_pre_maximum_event_week"]
    ):
        return "clean_pre"
    if int(design["anticipation_minimum_event_week"]) <= event_week <= int(
        design["anticipation_maximum_event_week"]
    ):
        return "anticipation_excluded"
    if event_week == 0:
        return "event_week_zero_excluded"
    return "post"


def build_common_panel(
    config: dict[str, Any],
    gnosis_weekly: pd.DataFrame,
    arbitrum_weekly: pd.DataFrame,
    calendar: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    required = {"event_week", "active_beneficiary_addresses", "beneficiary_hhi"}
    for name, frame in (("Gnosis", gnosis_weekly), ("Arbitrum", arbitrum_weekly)):
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"{name} weekly panel is missing columns: {missing}")
        weeks = sorted(frame["event_week"].astype(int).tolist())
        if weeks != list(range(-16, 17)):
            raise ValueError(f"{name} must have one row for every event week [-16,+16]")
    calendar_by_week = {int(row["event_week"]): row for row in calendar}
    rows: list[dict[str, Any]] = []
    for chain, chain_id, frame in (
        ("Arbitrum", 42161, arbitrum_weekly),
        ("Gnosis", 100, gnosis_weekly),
    ):
        for record in frame.to_dict(orient="records"):
            week = int(record["event_week"])
            period = calendar_by_week[week]
            active = int(record["active_beneficiary_addresses"])
            rows.append(
                {
                    "chain": chain,
                    "chain_id": chain_id,
                    "event_week": week,
                    "window_start_utc": period["window_start_utc"],
                    "window_end_utc_exclusive": period["window_end_utc_exclusive"],
                    "period": _period(config, week),
                    "event_count": int(record["event_count"]),
                    "active_beneficiary_addresses": active,
                    "log_active_beneficiary_addresses": math.log1p(active),
                    "beneficiary_hhi": float(record["beneficiary_hhi"]),
                    "observed_unit": "beneficiary_address",
                    "causal_status": CAUSAL_STATUS,
                }
            )
    rows.sort(key=lambda row: (int(row["event_week"]), int(row["chain_id"])))
    return rows


def stable_float(value: float, digits: int = SERIALIZATION_SIGNIFICANT_DIGITS) -> float:
    value = float(value)
    if not math.isfinite(value):
        return value
    return float(f"{value:.{digits}g}")


def _stable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: stable_float(value) if isinstance(value, float | np.floating) else value
        for key, value in row.items()
    }


def newey_west_ols(
    y: np.ndarray,
    x: np.ndarray,
    *,
    lag: int,
    time_index: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Fit OLS and compute a calendar-aware Bartlett-kernel HAC covariance.

    ``time_index`` records the actual integer event week for every row. This matters
    when design rows are deliberately excluded: weeks -9 and +1 must not be treated as
    adjacent observations merely because they are adjacent in the estimation matrix.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    n_obs, n_params = x.shape
    if y.shape != (n_obs,):
        raise ValueError("y and x must have the same number of observations")
    if time_index is None:
        time = np.arange(n_obs, dtype=int)
    else:
        time = np.asarray(time_index, dtype=int)
        if time.shape != (n_obs,):
            raise ValueError("time_index and x must have the same number of observations")
        if np.any(np.diff(time) <= 0):
            raise ValueError("time_index must be strictly increasing")
    xtx_inverse = np.linalg.pinv(x.T @ x)
    coefficients = xtx_inverse @ (x.T @ y)
    residuals = y - x @ coefficients
    meat = np.zeros((n_params, n_params), dtype=float)
    for index in range(n_obs):
        meat += residuals[index] ** 2 * np.outer(x[index], x[index])
    for index in range(n_obs):
        for previous in range(index):
            calendar_lag = int(time[index] - time[previous])
            if not 1 <= calendar_lag <= lag:
                continue
            weight = 1.0 - calendar_lag / (lag + 1.0)
            cross = np.outer(x[index], x[previous])
            meat += weight * residuals[index] * residuals[previous] * (cross + cross.T)
    covariance = xtx_inverse @ meat @ xtx_inverse
    if n_obs > n_params:
        covariance *= n_obs / (n_obs - n_params)
    return {
        "coefficients": coefficients,
        "standard_errors": np.sqrt(np.maximum(np.diag(covariance), 0.0)),
    }


def _normal_diagnostic(estimate: float, standard_error: float) -> dict[str, float]:
    if standard_error <= 0:
        return {
            "ci_lower": estimate,
            "ci_upper": estimate,
            "normal_approx_p_value": math.nan,
        }
    p_value = 2.0 * (1.0 - NormalDist().cdf(abs(estimate / standard_error)))
    return {
        "ci_lower": estimate - 1.96 * standard_error,
        "ci_upper": estimate + 1.96 * standard_error,
        "normal_approx_p_value": stable_float(p_value, PVALUE_SIGNIFICANT_DIGITS),
    }


def _outcome_pivot(panel: pd.DataFrame, outcome: str) -> pd.DataFrame:
    pivot = panel.pivot(index="event_week", columns="chain", values=outcome).sort_index()
    return pivot[["Arbitrum", "Gnosis"]]


def estimate_did(
    config: dict[str, Any], panel: pd.DataFrame, outcome: str, post_horizon: int
) -> dict[str, Any]:
    design = config["design"]
    pivot = _outcome_pivot(panel, outcome)
    pre_weeks = list(
        range(
            int(design["clean_pre_minimum_event_week"]),
            int(design["clean_pre_maximum_event_week"]) + 1,
        )
    )
    post_weeks = list(range(int(design["post_minimum_event_week"]), post_horizon + 1))
    selected = pivot.loc[pre_weeks + post_weeks]
    post = np.array([False] * len(pre_weeks) + [True] * len(post_weeks))
    means = {
        chain: {
            "pre": float(selected.loc[pre_weeks, chain].mean()),
            "post": float(selected.loc[post_weeks, chain].mean()),
        }
        for chain in ("Arbitrum", "Gnosis")
    }
    gap = (selected["Arbitrum"] - selected["Gnosis"]).to_numpy(dtype=float)
    fit = newey_west_ols(
        gap,
        np.column_stack([np.ones(len(selected), dtype=float), post.astype(float)]),
        lag=int(config["analysis"]["newey_west_lag"]),
        time_index=selected.index.to_numpy(dtype=int),
    )
    estimate = float(fit["coefficients"][1])
    standard_error = float(fit["standard_errors"][1])
    return _stable_row(
        {
            "outcome_id": outcome,
            "pre_event_weeks": f"{pre_weeks[0]}:{pre_weeks[-1]}",
            "post_event_weeks": f"{post_weeks[0]}:{post_weeks[-1]}",
            "post_horizon": post_horizon,
            "anticipation_weeks_excluded": "-8:-1",
            "event_week_zero_excluded": True,
            "arbitrum_pre_mean": means["Arbitrum"]["pre"],
            "arbitrum_post_mean": means["Arbitrum"]["post"],
            "arbitrum_change": means["Arbitrum"]["post"] - means["Arbitrum"]["pre"],
            "gnosis_pre_mean": means["Gnosis"]["pre"],
            "gnosis_post_mean": means["Gnosis"]["post"],
            "gnosis_change": means["Gnosis"]["post"] - means["Gnosis"]["pre"],
            "difference_in_changes_arbitrum_minus_gnosis": estimate,
            "nw_lag": int(config["analysis"]["newey_west_lag"]),
            "nw_standard_error": standard_error,
            **_normal_diagnostic(estimate, standard_error),
            "causal_status": CAUSAL_STATUS,
        }
    )


def pretrend_diagnostic(
    config: dict[str, Any], panel: pd.DataFrame, outcome: str
) -> dict[str, Any]:
    design = config["design"]
    pivot = _outcome_pivot(panel, outcome)
    weeks = list(
        range(
            int(design["clean_pre_minimum_event_week"]),
            int(design["clean_pre_maximum_event_week"]) + 1,
        )
    )
    gap = (pivot.loc[weeks, "Arbitrum"] - pivot.loc[weeks, "Gnosis"]).to_numpy(
        dtype=float
    )
    fit = newey_west_ols(
        gap,
        np.column_stack([np.ones(len(weeks), dtype=float), np.array(weeks, dtype=float)]),
        lag=int(config["analysis"]["pretrend_newey_west_lag"]),
        time_index=np.array(weeks, dtype=int),
    )
    slope = float(fit["coefficients"][1])
    standard_error = float(fit["standard_errors"][1])
    return _stable_row(
        {
            "outcome_id": outcome,
            "pre_event_weeks": f"{weeks[0]}:{weeks[-1]}",
            "gap_slope_per_event_week": slope,
            "nw_lag": int(config["analysis"]["pretrend_newey_west_lag"]),
            "nw_standard_error": standard_error,
            **_normal_diagnostic(slope, standard_error),
            "interpretation": "low_power_diagnostic_only",
        }
    )


def placebo_diagnostics(
    config: dict[str, Any], panel: pd.DataFrame, outcome: str
) -> list[dict[str, Any]]:
    pivot = _outcome_pivot(panel, outcome)
    gap = pivot["Arbitrum"] - pivot["Gnosis"]
    half = int(config["analysis"]["placebo_half_window"])
    rows = []
    for pseudo_week in config["analysis"]["placebo_event_weeks"]:
        pseudo = int(pseudo_week)
        before = gap.loc[pseudo - half : pseudo - 1]
        after = gap.loc[pseudo + 1 : pseudo + half]
        if len(before) != half or len(after) != half:
            raise ValueError(f"Incomplete placebo support around event week {pseudo}")
        rows.append(
            _stable_row(
                {
                    "outcome_id": outcome,
                    "pseudo_event_week": pseudo,
                    "half_window": half,
                    "gap_change": float(after.mean() - before.mean()),
                    "causal_status": "preperiod_placebo_diagnostic",
                }
            )
        )
    return rows


def event_study_rows(
    config: dict[str, Any], panel: pd.DataFrame, outcome: str
) -> list[dict[str, Any]]:
    pivot = _outcome_pivot(panel, outcome)
    pre = pivot.loc[-16:-9]
    baseline_gap = float((pre["Arbitrum"] - pre["Gnosis"]).mean())
    rows = []
    for week, values in pivot.iterrows():
        gap = float(values["Arbitrum"] - values["Gnosis"])
        rows.append(
            _stable_row(
                {
                    "outcome_id": outcome,
                    "event_week": int(week),
                    "period": _period(config, int(week)),
                    "arbitrum": float(values["Arbitrum"]),
                    "gnosis": float(values["Gnosis"]),
                    "gap_arbitrum_minus_gnosis": gap,
                    "pre_mean_gap": baseline_gap,
                    "gap_relative_to_clean_pre_mean": gap - baseline_gap,
                    "causal_status": CAUSAL_STATUS,
                }
            )
        )
    return rows


def render_event_study_svg(rows: list[dict[str, Any]], path: Path) -> Path:
    width, height = 1200, 520
    panel_width, panel_height = 500, 340
    lefts = [80, 660]
    top = 90
    outcomes = [
        ("log_active_beneficiary_addresses", "log(1 + active beneficiary addresses)"),
        ("beneficiary_hhi", "Beneficiary-event HHI"),
    ]
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="1200" height="520" fill="#ffffff"/>',
        '<text x="600" y="36" text-anchor="middle" font-family="Arial" '
        'font-size="24" font-weight="700" fill="#172554">Arbitrum–Gnosis '
        'common-calendar event study</text>',
        '<text x="600" y="62" text-anchor="middle" font-family="Arial" '
        'font-size="13" fill="#475569">Gap relative to the clean pre-period mean; '
        'diagnostic, not causal</text>',
    ]
    colors = {
        "clean_pre": "#2563eb",
        "anticipation_excluded": "#94a3b8",
        "event_week_zero_excluded": "#f97316",
        "post": "#7c3aed",
    }
    for panel_index, (outcome, title) in enumerate(outcomes):
        selected = [row for row in rows if row["outcome_id"] == outcome]
        values = [float(row["gap_relative_to_clean_pre_mean"]) for row in selected]
        lower, upper = min(values), max(values)
        padding = max((upper - lower) * 0.12, 0.02 if outcome == "beneficiary_hhi" else 0.1)
        lower -= padding
        upper += padding
        left = lefts[panel_index]

        def x_position(week: int, *, panel_left: float = left) -> float:
            return panel_left + (week + 16) / 32 * panel_width

        def y_position(
            value: float,
            *,
            panel_lower: float = lower,
            panel_upper: float = upper,
        ) -> float:
            return top + (panel_upper - value) / (panel_upper - panel_lower) * panel_height

        anticipation_x = x_position(-8)
        anticipation_width = x_position(0) - anticipation_x
        elements.append(
            f'<rect x="{anticipation_x:.2f}" y="{top}" width="{anticipation_width:.2f}" '
            f'height="{panel_height}" fill="#e2e8f0" opacity="0.55"/>'
        )
        zero_y = y_position(0.0)
        if top <= zero_y <= top + panel_height:
            elements.append(
                f'<line x1="{left}" y1="{zero_y:.2f}" x2="{left + panel_width}" '
                f'y2="{zero_y:.2f}" stroke="#64748b" stroke-dasharray="5 4"/>'
            )
        elements.extend(
            [
                f'<rect x="{left}" y="{top}" width="{panel_width}" '
                f'height="{panel_height}" fill="none" stroke="#cbd5e1"/>',
                f'<line x1="{x_position(0):.2f}" y1="{top}" x2="{x_position(0):.2f}" '
                f'y2="{top + panel_height}" stroke="#f97316" stroke-width="2"/>',
                f'<text x="{left + panel_width / 2}" y="82" text-anchor="middle" '
                f'font-family="Arial" font-size="16" font-weight="700" '
                f'fill="#1e293b">{html.escape(title)}</text>',
            ]
        )
        for tick_index in range(5):
            value = lower + tick_index * (upper - lower) / 4
            y = y_position(value)
            elements.append(
                f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" '
                f'font-family="Arial" font-size="11" fill="#475569">{value:.3f}</text>'
            )
        points = " ".join(
            f'{x_position(int(row["event_week"])):.2f},'
            f'{y_position(float(row["gap_relative_to_clean_pre_mean"])):.2f}'
            for row in selected
        )
        elements.append(
            f'<polyline points="{points}" fill="none" stroke="#312e81" '
            'stroke-width="2" stroke-linejoin="round"/>'
        )
        for row in selected:
            x = x_position(int(row["event_week"]))
            y = y_position(float(row["gap_relative_to_clean_pre_mean"]))
            color = colors[str(row["period"])]
            elements.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{color}" '
                'stroke="#ffffff" stroke-width="1"/>'
            )
        for tick in (-16, -8, 0, 8, 16):
            x = x_position(tick)
            elements.append(
                f'<text x="{x:.2f}" y="{top + panel_height + 24}" text-anchor="middle" '
                f'font-family="Arial" font-size="12" fill="#475569">{tick}</text>'
            )
        elements.append(
            f'<text x="{left + panel_width / 2}" y="{top + panel_height + 48}" '
            'text-anchor="middle" font-family="Arial" font-size="13" '
            'fill="#334155">Event week relative to Arbitrum GHO activation</text>'
        )
    elements.append(
        '<text x="600" y="500" text-anchor="middle" font-family="Arial" '
        'font-size="12" fill="#64748b">Gray: excluded anticipation period; orange: '
        'excluded week 0. Observed units are beneficiary addresses.</text>'
    )
    elements.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = "\n".join(elements).replace(
        'font-family="Arial"', 'font-family="Latin Modern Roman, serif"'
    )
    path.write_text(svg + "\n", encoding="utf-8")
    return path


def run_gnosis_donor_acquisition(
    root: str | Path = ".",
    *,
    config_path: str | Path = CONFIG_PATH,
    rpc_url: str | None = None,
    resume: bool = True,
) -> dict[str, Path]:
    project = Path(root).resolve()
    configuration_path = project / config_path
    config = load_gnosis_donor_config(configuration_path)
    donor = config["donor"]
    providers = config["providers"]
    retrieval = config["retrieval"]
    primary_url = _validated_url(
        rpc_url
        or _required_environment_url(str(providers["primary"]["environment_variable"])),
        label="primary RPC",
    )
    bulk_url = _validated_url(str(providers["bulk"]["url"]), label="bulk RPC")
    crosscheck_url = _validated_url(
        str(providers["log_crosscheck"]["url"]), label="log cross-check RPC"
    )
    endpoint_set = {
        safe_rpc_endpoint(primary_url),
        safe_rpc_endpoint(bulk_url),
        safe_rpc_endpoint(crosscheck_url),
    }
    if len(endpoint_set) != 3:
        raise ValueError("Gnosis acquisition requires three independent RPC endpoints")

    stats = {role: RpcStats() for role in ("primary", "bulk", "log_crosscheck")}

    def client(url: str, role: str) -> _RedactingRpcClient:
        return _RedactingRpcClient(
            url,
            timeout_seconds=float(retrieval["timeout_seconds"]),
            maximum_attempts=int(retrieval["maximum_attempts"]),
            stats=stats[role],
        )

    primary = client(primary_url, "primary")
    bulk = client(bulk_url, "bulk")
    log_crosscheck = client(crosscheck_url, "log_crosscheck")
    expected_chain_id = int(donor["chain_id"])
    observed_chain_ids = {
        "primary": primary.chain_id(),
        "bulk": bulk.chain_id(),
        "log_crosscheck": log_crosscheck.chain_id(),
    }
    if set(observed_chain_ids.values()) != {expected_chain_id}:
        raise ValueError(f"Gnosis RPC chain ID mismatch: {observed_chain_ids}")

    calendar = read_common_calendar(config, project)
    targets = boundary_targets(calendar)
    output = project / retrieval["output_directory"]
    output.mkdir(parents=True, exist_ok=True)
    boundary_path = output / "block_boundaries.csv"
    if resume and boundary_path.exists():
        boundaries = read_donor_boundary_cache(boundary_path, targets)
        boundary_mode = "validated_cache"
    else:
        boundaries = resolve_boundaries(
            primary,
            targets,
            activation_block=int(donor["anchor_block"]),
            activation_utc=parse_utc(str(donor["anchor_utc"])),
            workers=int(retrieval["boundary_workers"]),
            seed_seconds_per_block=int(retrieval["boundary_seed_seconds_per_block"]),
            initial_radius_blocks=int(retrieval["boundary_initial_radius_blocks"]),
        )
        write_csv_records(boundary_path, boundaries)
        boundary_mode = "rpc_exact_adjacent_headers_with_external_anchor"

    from .real_v2_ethereum import cross_provider_boundary_checks

    boundary_checks = []
    for role, validation_client in (
        ("bulk", bulk),
        ("log_crosscheck", log_crosscheck),
    ):
        checks = cross_provider_boundary_checks(boundaries, primary, validation_client)
        for row in checks:
            row["comparison"] = f"primary-vs-{role}"
            row["validation_source_id"] = providers[role]["source_id"]
        boundary_checks.extend(checks)
    boundary_check_path = write_csv_records(
        output / "boundary_provider_checks.csv", boundary_checks
    )

    first_block = int(boundaries[0]["start_block"])
    last_block = int(boundaries[-1]["start_block"]) - 1
    pool_address = str(donor["pool_address"]).lower()
    fingerprints = {
        "primary": _provider_fingerprint(
            primary, pool_address=pool_address, block_number=first_block
        ),
        "bulk": _provider_fingerprint(
            bulk, pool_address=pool_address, block_number=first_block
        ),
        "log_crosscheck": _provider_fingerprint(
            log_crosscheck, pool_address=pool_address, block_number=first_block
        ),
    }
    fingerprint_values = list(fingerprints.values())
    fingerprints_match = all(value == fingerprint_values[0] for value in fingerprint_values)
    if not fingerprints_match or not all(
        bool(value["pool_code_present"]) for value in fingerprint_values
    ):
        raise ValueError("Independent providers disagree on the Gnosis window-start state")
    fingerprint_path = _write_json(
        output / "provider_fingerprints.json",
        {
            "schema_version": 1,
            "block_number": first_block,
            "providers": fingerprints,
            "exact_match": fingerprints_match,
        },
    )

    query_width = int(retrieval["blocks_per_log_query"])
    probe_end = min(last_block, first_block + query_width - 1)
    filter_parameters = {
        "address": pool_address,
        "topics": [event_topics()],
        "fromBlock": hex(first_block),
        "toBlock": hex(probe_end),
    }
    bulk.logs(filter_parameters)
    validation_width = int(retrieval["validation_sample_width_blocks"])
    validation_filter = dict(filter_parameters)
    validation_filter["toBlock"] = hex(first_block + validation_width - 1)
    log_crosscheck.logs(validation_filter)

    chunks = build_chunks(
        first_block,
        last_block,
        int(retrieval["maximum_blocks_per_cache_chunk"]),
    )
    paced_bulk = _PacedLogClient(
        bulk,
        minimum_interval_seconds=float(
            retrieval["minimum_request_start_interval_seconds"]
        ),
        rate_limit_cooldown_seconds=float(retrieval["rate_limit_cooldown_seconds"]),
        maximum_rate_limit_cooldowns=int(retrieval["maximum_rate_limit_cooldowns"]),
    )
    chunk_directory = project / retrieval["raw_chunk_directory"]
    raw_logs, chunk_records = fetch_log_chunks(
        paced_bulk,
        chunks,
        pool_address=pool_address,
        topics=event_topics(),
        chunk_directory=chunk_directory,
        project_root=project,
        workers=int(retrieval["log_workers"]),
        resume=resume,
        progress_every=int(retrieval["progress_every_chunks"]),
        progress_interval_seconds=float(retrieval["progress_interval_seconds"]),
        initial_query_width=query_width,
        minimum_query_width=int(retrieval["minimum_adaptive_blocks_per_log_query"]),
        maximum_pending=int(retrieval["maximum_pending_log_chunks"]),
        maximum_runtime_seconds=float(retrieval["maximum_runtime_seconds"]),
    )
    for row in chunk_records:
        row["retrieval_mode"] = "bulk_public_rpc_or_topic_query"
        row["source_id"] = providers["bulk"]["source_id"]
    chunk_path = write_csv_records(output / "retrieval_chunks.csv", chunk_records)
    raw_log_hash = _canonical_json_list_sha256(raw_logs)

    log_checks, field_differences = cross_provider_consensus_checks(
        raw_logs,
        log_crosscheck,
        pool_address=pool_address,
        topics=event_topics(),
        sample_count=int(retrieval["validation_sample_count"]),
        sample_width=int(retrieval["validation_sample_width_blocks"]),
        minimum_block=first_block,
        maximum_block=last_block,
    )
    for row in log_checks:
        row["bulk_log_source_id"] = providers["bulk"]["source_id"]
        row["validation_source_id"] = providers["log_crosscheck"]["source_id"]
        row["verification_scope"] = "independent_provider"
    log_check_path = write_csv_records(output / "cross_provider_checks.csv", log_checks)
    for row in field_differences:
        row["bulk_log_source_id"] = providers["bulk"]["source_id"]
        row["validation_source_id"] = providers["log_crosscheck"]["source_id"]
    field_difference_path = write_csv_records(
        output / "cross_provider_field_differences.csv", field_differences
    )

    decoded = [
        decode_pool_log(log, chain_id=expected_chain_id, pool_address=pool_address)
        for log in raw_logs
    ]
    del raw_logs
    decoded = assign_event_weeks(decoded, boundaries)
    processed_path = project / retrieval["processed_event_path"]
    _write_processed_events(processed_path, decoded)
    frame = pd.DataFrame(decoded)
    weekly_rows = build_weekly_beneficiary_panel(
        frame,
        calendar,
        chain="Gnosis",
        chain_id=100,
        cohort_id=str(donor["cohort_id"]),
    )
    weekly_path = write_csv_records(output / "weekly_beneficiary_panel.csv", weekly_rows)
    action_counts = Counter(str(row["action"]) for row in decoded)
    event_count = len(decoded)
    transaction_count = len({str(row["tx_hash"]) for row in decoded})
    beneficiary_count = len(
        {str(row["beneficiary_address"]).lower() for row in decoded}
    )
    del decoded, frame

    provider_gate = (
        len(endpoint_set) == 3
        and fingerprints_match
        and all(bool(row["exact_match"]) for row in boundary_checks)
        and all(bool(row["exact_match"]) for row in log_checks)
    )
    summary = {
        "schema_version": 1,
        "release_version": config["release_version"],
        "status": "audited_donor_input" if provider_gate else "provider_gate_pending",
        "chain": "Gnosis",
        "chain_id": 100,
        "calendar_treatment_chain": "Arbitrum",
        "calendar_treatment_utc": config["design"]["treatment_utc"],
        "window": {
            "minimum_event_week": -16,
            "maximum_event_week": 16,
            "start_utc": boundaries[0]["target_utc"],
            "end_utc_exclusive": boundaries[-1]["target_utc"],
            "first_block": first_block,
            "last_block": last_block,
        },
        "event_count": event_count,
        "transaction_count": transaction_count,
        "beneficiary_address_count": beneficiary_count,
        "action_counts": dict(sorted(action_counts.items())),
        "retrieval_chunk_count": len(chunk_records),
        "raw_log_canonical_sha256": raw_log_hash,
        "independent_provider_gate_passed": provider_gate,
        "causal_estimate_produced": False,
        "entity_level_primary_result_produced": False,
        "observed_unit": "beneficiary_address",
        "limitations": [
            "Beneficiary addresses are not verified people or economic actors.",
            "The public bulk RPC is paced and has no uptime guarantee.",
            "One treated chain and one donor chain cannot support chain-cluster inference.",
            "This acquisition layer does not itself produce a treatment effect.",
        ],
    }
    summary_path = _write_json(output / "summary.json", summary)
    tracked = [
        boundary_path,
        boundary_check_path,
        fingerprint_path,
        chunk_path,
        log_check_path,
        field_difference_path,
        weekly_path,
        summary_path,
    ]
    manifest = {
        "schema_version": 1,
        "pipeline": "real_v6_gnosis_common_calendar_donor",
        "release_version": config["release_version"],
        "source_revision": source_revision(project),
        "configuration": {
            "path": project_relative_path(configuration_path, project),
            "sha256": sha256_file(configuration_path),
        },
        "calendar": {
            "path": str(config["design"]["calendar"]),
            "sha256": sha256_file(project / config["design"]["calendar"]),
            "treated_cohort_id": config["design"]["treated_cohort_id"],
        },
        "providers": {
            role: {
                "source_id": providers[role]["source_id"],
                "endpoint": safe_rpc_endpoint(url),
            }
            for role, url in (
                ("primary", primary_url),
                ("bulk", bulk_url),
                ("log_crosscheck", crosscheck_url),
            )
        },
        "rpc_statistics": {role: value.to_dict() for role, value in stats.items()},
        "boundary_retrieval_mode": boundary_mode,
        "raw_log_canonical_sha256": raw_log_hash,
        "processed_event_canonical_csv_sha256": gzip_payload_sha256(processed_path),
        "raw_chunks": chunk_records,
        "artifacts": {
            project_relative_path(path, project): sha256_file(path) for path in tracked
        },
        "source_code": {
            relative: sha256_file(project / relative)
            for relative in (
                "src/aave_bns/evm_rpc.py",
                "src/aave_bns/aave_v3_events.py",
                "src/aave_bns/real_v2_ethereum.py",
                "src/aave_bns/real_v5_arbitrum.py",
                "src/aave_bns/real_v6_gnosis_donor.py",
                "scripts/run_real_v6_gnosis_donor.py",
            )
        },
        "independent_provider_gate_passed": provider_gate,
        "causal_estimate_produced": False,
    }
    manifest_path = output / "manifest.json"
    write_manifest(manifest_path, manifest)
    return {
        "boundaries": boundary_path,
        "boundary_checks": boundary_check_path,
        "provider_fingerprints": fingerprint_path,
        "chunks": chunk_path,
        "processed_events": processed_path,
        "cross_provider_checks": log_check_path,
        "cross_provider_field_differences": field_difference_path,
        "weekly_panel": weekly_path,
        "summary": summary_path,
        "manifest": manifest_path,
    }


def run_arbitrum_gnosis_did_mvp(
    root: str | Path = ".", *, config_path: str | Path = CONFIG_PATH
) -> dict[str, Path]:
    project = Path(root).resolve()
    configuration_path = project / config_path
    config = load_gnosis_donor_config(configuration_path)
    acquisition_output = project / config["retrieval"]["output_directory"]
    acquisition_summary = json.loads(
        (acquisition_output / "summary.json").read_text(encoding="utf-8")
    )
    if not bool(acquisition_summary["independent_provider_gate_passed"]):
        raise ValueError("Gnosis donor input has not passed the independent provider gate")
    gnosis = pd.read_csv(acquisition_output / "weekly_beneficiary_panel.csv")
    metrics_path = project / config["analysis"]["arbitrum_weekly_metrics"]
    metrics = pd.read_csv(metrics_path)
    arbitrum = metrics.loc[metrics["chain"] == "Arbitrum"].copy()
    calendar = read_common_calendar(config, project)
    panel_rows = build_common_panel(config, gnosis, arbitrum, calendar)
    output = project / config["analysis"]["output_directory"]
    panel_path = _write_csv(output / "common_calendar_panel.csv", panel_rows)
    panel = pd.DataFrame(panel_rows)
    outcomes = [str(value) for value in config["analysis"]["outcomes"]]
    horizons = [int(value) for value in config["analysis"]["sensitivity_post_horizons"]]
    estimates = [
        estimate_did(config, panel, outcome, horizon)
        for outcome in outcomes
        for horizon in horizons
    ]
    pretrends = [pretrend_diagnostic(config, panel, outcome) for outcome in outcomes]
    placebos = [
        row
        for outcome in outcomes
        for row in placebo_diagnostics(config, panel, outcome)
    ]
    event_rows = [
        row for outcome in outcomes for row in event_study_rows(config, panel, outcome)
    ]
    estimate_path = _write_csv(output / "estimates.csv", estimates)
    pretrend_path = _write_csv(output / "pretrend_diagnostics.csv", pretrends)
    placebo_path = _write_csv(output / "preperiod_placebos.csv", placebos)
    event_path = _write_csv(output / "event_study.csv", event_rows)
    figure_path = render_event_study_svg(event_rows, output / "event_study.svg")
    primary_horizon = int(config["analysis"]["primary_post_horizon"])
    primary = {
        str(row["outcome_id"]): row
        for row in estimates
        if int(row["post_horizon"]) == primary_horizon
    }
    summary = {
        "schema_version": 1,
        "release_version": config["release_version"],
        "status": "common_calendar_failed_donor_diagnostic",
        "treated_chain": "Arbitrum",
        "donor_chain": "Gnosis",
        "treatment_utc": config["design"]["treatment_utc"],
        "clean_pre_event_weeks": [-16, -9],
        "anticipation_event_weeks_excluded": [-8, -1],
        "event_week_zero_excluded": True,
        "post_event_weeks": [1, 16],
        "common_calendar_panel_rows": len(panel_rows),
        "primary_post_horizon": primary_horizon,
        "primary_results": primary,
        "pretrend_diagnostics": {str(row["outcome_id"]): row for row in pretrends},
        "preperiod_placebo_max_abs": {
            outcome: max(
                abs(float(row["gap_change"]))
                for row in placebos
                if row["outcome_id"] == outcome
            )
            for outcome in outcomes
        },
        "gnosis_independent_provider_gate_passed": True,
        "diagnostic_estimate_produced": True,
        "causal_estimate_produced": False,
        "causal_language_permitted": False,
        "identification_limitations": [
            "only one treated chain and one donor chain are observed",
            "chain-cluster causal inference is unavailable",
            "parallel trends can only be assessed with an eight-week low-power diagnostic",
            "position-holder addresses are protocol-observed proxies, not verified actors",
        ],
        "interpretation": (
            "The arithmetic contrasts are common-calendar Arbitrum-minus-Gnosis "
            "difference-in-changes diagnostics. Gnosis fails the donor-support and pre-path "
            "gates, so the exercise is a falsification result, not policy evidence."
        ),
    }
    summary_path = _write_json(output / "summary.json", summary)
    manifest = {
        "schema_version": 1,
        "pipeline": "real_v6_arbitrum_gnosis_did_mvp",
        "release_version": config["release_version"],
        "source_revision": source_revision(project),
        "inputs": {
            project_relative_path(metrics_path, project): sha256_file(metrics_path),
            project_relative_path(
                acquisition_output / "weekly_beneficiary_panel.csv", project
            ): sha256_file(acquisition_output / "weekly_beneficiary_panel.csv"),
            project_relative_path(
                acquisition_output / "summary.json", project
            ): sha256_file(acquisition_output / "summary.json"),
        },
        "artifacts": {
            project_relative_path(path, project): sha256_file(path)
            for path in (
                panel_path,
                estimate_path,
                pretrend_path,
                placebo_path,
                event_path,
                figure_path,
                summary_path,
            )
        },
        "causal_estimate_produced": False,
    }
    manifest_path = output / "manifest.json"
    write_manifest(manifest_path, manifest)
    return {
        "panel": panel_path,
        "estimates": estimate_path,
        "pretrends": pretrend_path,
        "placebos": placebo_path,
        "event_study": event_path,
        "figure": figure_path,
        "summary": summary_path,
        "manifest": manifest_path,
    }
