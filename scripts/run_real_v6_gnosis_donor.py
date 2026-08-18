from __future__ import annotations

import argparse

from aave_bns.real_v6_gnosis_donor import (
    run_arbitrum_gnosis_did_mvp,
    run_gnosis_donor_acquisition,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Acquire the Gnosis donor panel and run the Arbitrum–Gnosis MVP"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v6_gnosis_donor.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire")
    acquire.add_argument("--rpc-url")
    acquire.add_argument("--no-resume", action="store_true")
    subparsers.add_parser("did")

    args = parser.parse_args()
    if args.command == "acquire":
        outputs = run_gnosis_donor_acquisition(
            args.root,
            config_path=args.config,
            rpc_url=args.rpc_url,
            resume=not args.no_resume,
        )
    else:
        outputs = run_arbitrum_gnosis_did_mvp(
            args.root,
            config_path=args.config,
        )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
