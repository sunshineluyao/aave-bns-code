from __future__ import annotations

import argparse

from aave_bns.real_v5_core_periphery import run_core_periphery_analysis


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build audited Ethereum-Arbitrum address-role core-periphery outputs"
    )
    parser.add_argument("--ethereum", required=True)
    parser.add_argument("--arbitrum", required=True)
    parser.add_argument("--output", default="outputs/real_v5/core_periphery")
    parser.add_argument(
        "--locked-metrics",
        default="outputs/real_v5/topology/address_role_topology_metrics.csv",
    )
    args = parser.parse_args()
    outputs = run_core_periphery_analysis(
        args.ethereum,
        args.arbitrum,
        args.output,
        locked_metrics_path=args.locked_metrics,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
