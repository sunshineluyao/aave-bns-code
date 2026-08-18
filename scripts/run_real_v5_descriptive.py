from __future__ import annotations

import argparse

from aave_bns.real_v5_descriptive import run_descriptive_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gated real_v5 address-level metrics")
    parser.add_argument("--ethereum", required=True)
    parser.add_argument("--arbitrum", required=True)
    parser.add_argument("--output", default="outputs/real_v5/descriptive")
    args = parser.parse_args()
    for name, path in run_descriptive_analysis(args.ethereum, args.arbitrum, args.output).items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
