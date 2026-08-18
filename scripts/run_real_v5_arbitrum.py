from __future__ import annotations

import argparse

from aave_bns.real_v5_arbitrum import run_real_v5_arbitrum_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Ethereum-Arbitrum real_v5 descriptive candidate"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v5_arbitrum.yaml")
    parser.add_argument("--rpc-url")
    parser.add_argument("--validation-rpc-url")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    outputs = run_real_v5_arbitrum_candidate(
        args.root,
        config_path=args.config,
        rpc_url=args.rpc_url,
        validation_rpc_url=args.validation_rpc_url,
        resume=not args.no_resume,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
