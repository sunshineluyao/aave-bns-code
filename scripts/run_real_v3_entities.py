from __future__ import annotations

import argparse
import json

from aave_bns.real_v3_entities import run_real_v3_entity_layer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the audited Ethereum real_v3 contract-role/entity layer"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/real_v3_ethereum.yaml")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-query every historical code batch instead of reusing validated raw chunks",
    )
    args = parser.parse_args()
    result = run_real_v3_entity_layer(
        args.config,
        project_root=args.root,
        resume=not args.no_resume,
    )
    summary = result["summary"]
    print(
        json.dumps(
            {
                "release_version": summary["release_version"],
                "address_count": summary["address_count"],
                "smart_contract_address_count": summary["smart_contract_address_count"],
                "contract_event_incidence_share": summary[
                    "contract_event_incidence_share"
                ],
                "curated_label_event_incidence_coverage": summary[
                    "curated_label_event_incidence_coverage"
                ],
                "entity_gate_passed": summary["entity_gate"]["passed"],
                "causal_estimate_produced": summary["causal_estimate_produced"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
