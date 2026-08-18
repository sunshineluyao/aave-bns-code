from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _gini(counts: np.ndarray) -> float:
    values = np.sort(counts.astype(float))
    if values.size == 0 or values.sum() == 0:
        return 0.0
    index = np.arange(1, values.size + 1)
    return float(
        (2 * np.sum(index * values) / (values.size * values.sum()))
        - (values.size + 1) / values.size
    )


def _nakamoto(counts: np.ndarray, threshold: float = 0.51) -> int:
    if counts.size == 0 or counts.sum() == 0:
        return 0
    shares = np.sort(counts.astype(float) / counts.sum())[::-1]
    return int(np.searchsorted(np.cumsum(shares), threshold, side="left") + 1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_events(path: Path, chain: str, chain_id: int) -> pd.DataFrame:
    usecols = ["event_week", "action", "beneficiary_address"]
    frame = pd.read_csv(path, usecols=usecols)
    frame["beneficiary_address"] = frame["beneficiary_address"].astype(str).str.lower()
    frame["event_week"] = frame["event_week"].astype(int)
    frame["chain"] = chain
    frame["chain_id"] = chain_id
    return frame


def weekly_address_metrics(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    prior: set[str] = set()
    for week in range(-16, 17):
        group = events.loc[events["event_week"] == week]
        counts = group["beneficiary_address"].value_counts().to_numpy()
        active = set(group["beneficiary_address"])
        total = int(counts.sum())
        shares = counts / total if total else np.array([], dtype=float)
        rows.append(
            {
                "chain": str(events["chain"].iloc[0]),
                "chain_id": int(events["chain_id"].iloc[0]),
                "event_week": week,
                "event_count": total,
                "active_beneficiary_addresses": len(active),
                "entrant_addresses": len(active - prior),
                "exit_addresses": len(prior - active),
                "retained_addresses": len(active & prior),
                "beneficiary_hhi": float(np.square(shares).sum()) if total else 0.0,
                "beneficiary_gini": _gini(counts),
                "top1pct_share": (
                    float(shares[: max(1, int(np.ceil(len(shares) * 0.01)))].sum())
                    if total
                    else 0.0
                ),
                "top10pct_share": (
                    float(shares[: max(1, int(np.ceil(len(shares) * 0.10)))].sum())
                    if total
                    else 0.0
                ),
                "nakamoto_51": _nakamoto(counts),
                "observed_unit": "beneficiary_address",
                "causal_status": "descriptive_only",
            }
        )
        prior = active
    return pd.DataFrame(rows)


def cross_chain_overlap(ethereum: pd.DataFrame, arbitrum: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for week in range(-16, 17):
        eth = set(ethereum.loc[ethereum["event_week"] == week, "beneficiary_address"])
        arb = set(arbitrum.loc[arbitrum["event_week"] == week, "beneficiary_address"])
        union = eth | arb
        rows.append(
            {
                "event_week": week,
                "ethereum_addresses": len(eth),
                "arbitrum_addresses": len(arb),
                "shared_addresses": len(eth & arb),
                "jaccard_overlap": len(eth & arb) / len(union) if union else 0.0,
                "ethereum_share_also_on_arbitrum": len(eth & arb) / len(eth) if eth else 0.0,
                "arbitrum_share_also_on_ethereum": len(eth & arb) / len(arb) if arb else 0.0,
                "timing_note": "event weeks are chain-relative, not simultaneous calendar weeks",
            }
        )
    return pd.DataFrame(rows)


def run_descriptive_analysis(
    ethereum_path: str | Path,
    arbitrum_path: str | Path,
    output_directory: str | Path,
) -> dict[str, Path]:
    ethereum_path = Path(ethereum_path)
    arbitrum_path = Path(arbitrum_path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    ethereum = _load_events(ethereum_path, "Ethereum", 1)
    arbitrum = _load_events(arbitrum_path, "Arbitrum", 42161)
    metrics = pd.concat(
        [weekly_address_metrics(ethereum), weekly_address_metrics(arbitrum)],
        ignore_index=True,
    )
    overlap = cross_chain_overlap(ethereum, arbitrum)
    metric_path = output / "weekly_address_metrics.csv"
    overlap_path = output / "cross_chain_overlap.csv"
    metrics.to_csv(metric_path, index=False)
    overlap.to_csv(overlap_path, index=False)
    summary = {
        "schema_version": 1,
        "status": "audited_address_level_descriptive",
        "input_sha256": {
            "ethereum_beneficiary_event_panel": _sha256(ethereum_path),
            "arbitrum_aave_v3_pool_actions": _sha256(arbitrum_path),
        },
        "chains": ["Ethereum", "Arbitrum"],
        "event_week_range": [-16, 16],
        "weekly_metric_rows": int(len(metrics)),
        "overlap_rows": int(len(overlap)),
        "causal_estimate_produced": False,
        "entity_level_primary_result_produced": False,
        "structural_network_result_produced": False,
        "infrastructure_dependence_result_produced": False,
        "withheld_reason": (
            "The released Ethereum event panel lacks a symmetric actor-beneficiary-reserve "
            "topology and neither chain release contains a bridge-route table."
        ),
        "interpretation": (
            "Addresses are protocol-observed position-holder addresses, not verified people "
            "or economic actors; event weeks are relative to chain-specific activation dates."
        ),
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return {"metrics": metric_path, "overlap": overlap_path, "summary": summary_path}
