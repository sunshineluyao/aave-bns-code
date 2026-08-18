from __future__ import annotations

import argparse

from aave_bns.real_v2_ethereum import run_ethereum_action_panel


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the locked Ethereum ±16-week Aave V3 Pool action panel"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v2_ethereum.yaml")
    parser.add_argument("--rpc-url")
    parser.add_argument("--validation-rpc-url")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--boundaries-only", action="store_true")
    args = parser.parse_args()
    outputs = run_ethereum_action_panel(
        args.root,
        config_path=args.config,
        rpc_url=args.rpc_url,
        validation_rpc_url=args.validation_rpc_url,
        resume=not args.no_resume,
        boundaries_only=args.boundaries_only,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
