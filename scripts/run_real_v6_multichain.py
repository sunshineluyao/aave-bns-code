from __future__ import annotations

import argparse
from pathlib import Path

from aave_bns.real_v6_multichain import (
    load_multichain_config,
    run_chain_preflight,
    validate_against_locked_sources,
    write_support_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real_v6 multichain support and RPC gates")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v6_multichain.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    support = subparsers.add_parser("support-audit")
    support.add_argument("--output", default="outputs/real_v6/support")

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument(
        "--chain",
        required=True,
        choices=("base", "avalanche", "gnosis", "mantle"),
    )
    preflight.add_argument("--rpc-url")
    preflight.add_argument("--public-rpc-url")
    preflight.add_argument("--output", default="outputs/real_v6/preflight")

    args = parser.parse_args()
    project = Path(args.root).resolve()
    config_path = project / args.config
    config = load_multichain_config(config_path)
    validate_against_locked_sources(config, project)
    if args.command == "support-audit":
        outputs = write_support_audit(config, project / args.output)
    else:
        outputs = run_chain_preflight(
            config,
            args.chain,
            project / args.output,
            rpc_url=args.rpc_url,
            public_rpc_url=args.public_rpc_url,
        )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
