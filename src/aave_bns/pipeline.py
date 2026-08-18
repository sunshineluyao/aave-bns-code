from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .causal import two_by_two_did
from .network import temporal_metrics
from .provenance import sha256_file, utc_now_iso, write_manifest
from .reporting import write_demo_result_table, write_treatment_table
from .transform import apply_entity_map, validate_transfers

ZERO = "0x" + "0" * 40
AAVE = "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"
GHO = "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f"


def _address(index: int) -> str:
    return "0x" + format(index, "040x")


def build_synthetic_fixture() -> pd.DataFrame:
    """Create a deterministic fixture that exercises the whole pipeline.

    This fixture is deliberately synthetic and must never be cited as paper evidence.
    """
    rng = np.random.default_rng(20260801)
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2024-05-05", tz="UTC")
    for week in range(16):
        post = week >= 8
        timestamp = start + pd.to_timedelta(int(week), unit="W")
        for chain_id, asset, token, treated in [(1, "GHO", GHO, 0), (42161, "GHO", GHO, 1)]:
            participants = 6 + week // 4 + (3 if treated and post else 0)
            hub_weight = 8.0 if not (treated and post) else 4.5
            for n in range(participants):
                source = _address(chain_id * 1000 + n + 1)
                target = _address(chain_id * 1000 + ((n + 1) % participants) + 1)
                value = float(rng.uniform(1.0, 3.0))
                rows.append({
                    "timestamp": timestamp,
                    "block_number": 1_000_000 + week * 100 + n,
                    "tx_hash": "0x" + format(chain_id * 10_000_000 + week * 1000 + n, "064x"),
                    "log_index": n,
                    "chain_id": chain_id,
                    "asset": asset,
                    "token_address": token,
                    "from_address": source,
                    "to_address": target,
                    "value": value,
                    "treated": treated,
                    "post": int(post),
                })
                rows.append({
                    "timestamp": timestamp + pd.to_timedelta(int(n) + 1, unit="m"),
                    "block_number": 1_000_050 + week * 100 + n,
                    "tx_hash": "0x" + format(chain_id * 20_000_000 + week * 1000 + n, "064x"),
                    "log_index": participants + n,
                    "chain_id": chain_id,
                    "asset": asset,
                    "token_address": token,
                    "from_address": source,
                    "to_address": _address(chain_id * 1000 + 1),
                    "value": hub_weight * float(rng.uniform(0.8, 1.2)),
                    "treated": treated,
                    "post": int(post),
                })
    return validate_transfers(pd.DataFrame(rows))


def run_demo(root: str | Path = ".") -> dict[str, Path]:
    project = Path(root).resolve()
    sample_dir = project / "data" / "sample"
    processed_dir = project / "data" / "processed"
    output_dir = project / "outputs"
    sample_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics").mkdir(parents=True, exist_ok=True)
    (output_dir / "causal").mkdir(parents=True, exist_ok=True)

    raw_path = sample_dir / "synthetic_transfers.csv"
    fixture = build_synthetic_fixture()
    fixture.to_csv(raw_path, index=False)

    transformed = apply_entity_map(fixture, mapping=None)
    transformed_path = processed_dir / "synthetic_transfers_entity_level.csv"
    transformed.to_csv(transformed_path, index=False)

    metrics = temporal_metrics(transformed)
    metrics_path = output_dir / "metrics" / "network_metrics.csv"
    metrics.to_csv(metrics_path, index=False)

    causal_panel = metrics.copy()
    causal_panel["treated"] = (causal_panel["chain_id"] == 42161).astype(int)
    treatment_period = causal_panel["period"].sort_values().unique()[8]
    causal_panel["post"] = (causal_panel["period"] >= treatment_period).astype(int)
    result = two_by_two_did(causal_panel, outcome="effective_entities")
    result_path = output_dir / "causal" / "did_results.json"
    result_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")

    write_treatment_table(
        project / "data" / "metadata" / "treatment_registry.csv",
        project / "paper" / "generated" / "tables" / "policy_events.tex",
    )
    write_demo_result_table(
        result_path,
        project / "paper" / "generated" / "tables" / "synthetic_ci.tex",
    )

    manifest_path = output_dir / "manifest.json"
    write_manifest(
        manifest_path,
        {
            "project": "aave-bns",
            "pipeline_version": "0.1.0",
            "synthetic": True,
            "warning": "CI fixture only; not empirical evidence.",
            "generated_at": utc_now_iso(),
            "artifacts": {
                str(path.relative_to(project)): sha256_file(path)
                for path in [raw_path, transformed_path, metrics_path, result_path]
            },
        },
    )
    return {
        "raw": raw_path,
        "transformed": transformed_path,
        "metrics": metrics_path,
        "causal": result_path,
        "manifest": manifest_path,
    }


def generate_policy_assets(root: str | Path = ".") -> Path:
    project = Path(root).resolve()
    return write_treatment_table(
        project / "data" / "metadata" / "treatment_registry.csv",
        project / "paper" / "generated" / "tables" / "policy_events.tex",
    )
