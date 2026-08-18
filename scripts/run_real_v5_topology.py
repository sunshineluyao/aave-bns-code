from __future__ import annotations

import argparse

from aave_bns.real_v5_topology import run_topology_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gated real_v5 address-role topology")
    parser.add_argument("--ethereum", required=True)
    parser.add_argument("--arbitrum", required=True)
    parser.add_argument("--output", default="outputs/real_v5/topology")
    args = parser.parse_args()
    for name, path in run_topology_analysis(args.ethereum, args.arbitrum, args.output).items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
