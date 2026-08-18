from __future__ import annotations

import argparse
import json

from aave_bns.real_v4_partial_identification import (
    run_real_v4_partial_identification,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the audited Ethereum real_v4 partial-identification release"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v4_ethereum.yaml")
    args = parser.parse_args()
    result = run_real_v4_partial_identification(
        args.config,
        project_root=args.root,
    )
    summary = result["summary"]
    print(
        json.dumps(
            {
                "release_version": summary["release_version"],
                "event_count": summary["event_count"],
                "beneficiary_address_count": summary["beneficiary_address_count"],
                "full_address_proxy_hhi": summary["full_address_proxy_hhi"],
                "actor_direction_identified": summary[
                    "economic_actor_direction_identified"
                ],
                "causal_estimate_produced": summary["causal_estimate_produced"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
