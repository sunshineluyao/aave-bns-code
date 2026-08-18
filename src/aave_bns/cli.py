from __future__ import annotations

import argparse
import os

from .config import load_yaml
from .pipeline import generate_policy_assets, run_demo
from .query import query_bigquery_token_transfers, rpc_get_logs
from .simulation import run_simulation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aave-bns")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Run the deterministic synthetic end-to-end check")
    demo.add_argument("--root", default=".")

    policy = sub.add_parser(
        "policy-assets",
        help="Generate paper assets from the treatment registry",
    )
    policy.add_argument("--root", default=".")

    simulation = sub.add_parser(
        "simulate",
        help="Run the deterministic stylized network-game mechanism check",
    )
    simulation.add_argument("--root", default=".")
    simulation.add_argument("--config", default="configs/simulation.yaml")

    query = sub.add_parser("query", help="Query public Ethereum token transfers from BigQuery")
    query.add_argument("--config", default="configs/analysis.yaml")
    query.add_argument("--contracts", default="configs/contracts.yaml")
    query.add_argument("--output", required=True)
    query.add_argument("--project", default=os.getenv("GCP_PROJECT"))

    rpc = sub.add_parser("rpc-logs", help="Fetch contract logs from a configured EVM endpoint")
    rpc.add_argument("--rpc-url", default=os.getenv("EVM_RPC_URL"))
    rpc.add_argument("--contract", required=True)
    rpc.add_argument("--topic0", required=True)
    rpc.add_argument("--from-block", type=int, required=True)
    rpc.add_argument("--to-block", type=int, required=True)
    rpc.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        outputs = run_demo(args.root)
        for name, path in outputs.items():
            print(f"{name}: {path}")
        return 0
    if args.command == "policy-assets":
        print(generate_policy_assets(args.root))
        return 0
    if args.command == "simulate":
        outputs = run_simulation(args.root, args.config)
        for name, path in outputs.items():
            print(f"{name}: {path}")
        return 0
    if args.command == "query":
        if not args.project:
            raise SystemExit("Set GCP_PROJECT or pass --project")
        analysis = load_yaml(args.config)
        contracts = load_yaml(args.contracts)
        asset_metadata = {
            asset: contracts["assets"][asset]
            for asset in analysis["query"]["assets"]
        }
        path = query_bigquery_token_transfers(
            sql_path="queries/ethereum/token_transfers.sql",
            output_path=args.output,
            project=args.project,
            start_date=analysis["query"]["start_date"],
            end_date=analysis["query"]["end_date"],
            asset_metadata=asset_metadata,
        )
        print(path)
        return 0
    if args.command == "rpc-logs":
        if not args.rpc_url:
            raise SystemExit("Set EVM_RPC_URL or pass --rpc-url")
        path = rpc_get_logs(
            rpc_url=args.rpc_url,
            contract_address=args.contract,
            topic0=args.topic0,
            from_block=args.from_block,
            to_block=args.to_block,
            output_path=args.output,
        )
        print(path)
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
