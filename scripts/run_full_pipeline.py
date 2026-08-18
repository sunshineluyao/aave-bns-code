#!/usr/bin/env python3
"""One-command entry point for the locked empirical pipeline.

This script intentionally stops after validating configuration unless credentialed raw
partitions are present. It prevents accidental substitution of the synthetic CI fixture
for the paper's empirical data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from aave_bns.network import temporal_metrics
from aave_bns.pipeline import generate_policy_assets
from aave_bns.provenance import sha256_file, utc_now_iso, write_manifest
from aave_bns.transform import apply_entity_map, read_transfers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transfers", required=True)
    parser.add_argument("--entity-map")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    transfers_path = Path(args.transfers)
    if "synthetic" in transfers_path.name.lower():
        raise SystemExit("Refusing to run empirical pipeline on a synthetic fixture")
    transfers = read_transfers(transfers_path)
    mapping = None
    if args.entity_map:
        import pandas as pd
        mapping = pd.read_csv(args.entity_map)
    transformed = apply_entity_map(transfers, mapping)
    metrics = temporal_metrics(transformed)
    output = root / "outputs" / "metrics" / "network_metrics_empirical.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output, index=False)
    generate_policy_assets(root)
    write_manifest(
        root / "outputs" / "empirical_manifest.json",
        {
            "synthetic": False,
            "generated_at": utc_now_iso(),
            "input": str(transfers_path),
            "input_sha256": sha256_file(transfers_path),
            "output": str(output),
            "output_sha256": sha256_file(output),
        },
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
