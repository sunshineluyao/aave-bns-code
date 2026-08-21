# Arbitrum acquisition

The Arbitrum observations originate from Aave V3 Pool event logs on Arbitrum One (chain ID 42161), retrieved with JSON-RPC `eth_getLogs`. The locked Pool address, event topics, block-window rules, provider cross-checks, and output paths are in `configs/real_v5_arbitrum.yaml`; decoding and acquisition are implemented in `src/aave_bns/real_v5_arbitrum.py`.

```bash
export ARBITRUM_RPC_URL="https://YOUR_PRIMARY_ARBITRUM_RPC"
PYTHONPATH=src python scripts/run_real_v5_arbitrum.py
```

The command writes raw chunks under `data/raw/real_v5/arbitrum/pool_action_chunks/` and decoded events to `data/processed/real_v5/arbitrum/aave_v3_pool_actions.csv.gz`. It is an explicit refresh path and is never run by `make reproduce-release`. The public release currently exposes governed processed Arbitrum configurations and acquisition receipts; it does not claim that a separate row-level queried Arbitrum partition is published.
