from __future__ import annotations

import argparse
import os

from aave_bns.ccip_rpc_extract import EventQuery, write_extraction_artifacts
from aave_bns.evm_rpc import RpcClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract one pinned CCIP event stream")
    parser.add_argument("--rpc-environment-variable", required=True)
    parser.add_argument("--chain-id", type=int, required=True)
    parser.add_argument("--contract-address", required=True)
    parser.add_argument("--topic0", required=True)
    parser.add_argument("--start-block", type=int, required=True)
    parser.add_argument("--end-block", type=int, required=True)
    parser.add_argument("--message-id-topic-index", type=int, required=True)
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    rpc_url = os.environ.get(args.rpc_environment_variable)
    if not rpc_url:
        raise SystemExit(f"{args.rpc_environment_variable} is not configured")
    query = EventQuery(
        chain_id=args.chain_id,
        contract_address=args.contract_address,
        topic0=args.topic0,
        start_block=args.start_block,
        end_block=args.end_block,
        message_id_topic_index=args.message_id_topic_index,
        chunk_size=args.chunk_size,
    )
    for path in write_extraction_artifacts(RpcClient(rpc_url), query, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
