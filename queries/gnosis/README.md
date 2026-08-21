# Gnosis acquisition

The Gnosis observations originate from Aave V3 Pool event logs on Gnosis Chain (chain ID 100), retrieved with JSON-RPC `eth_getLogs`. The locked Pool address, event topics, common calendar, provider cross-checks, and output paths are in `configs/real_v6_gnosis_donor.yaml`; decoding and acquisition are implemented in `src/aave_bns/real_v6_gnosis_donor.py`.

```bash
export GNOSIS_RPC_URL="https://YOUR_PRIMARY_GNOSIS_RPC"
PYTHONPATH=src python scripts/run_real_v6_gnosis_donor.py
```

The command writes raw chunks under `data/raw/real_v6/gnosis_donor/pool_action_chunks/` and decoded events to `data/processed/real_v6/gnosis_donor/aave_v3_pool_actions.csv.gz`. It is an explicit refresh path and is never run by `make reproduce-release`. The governed queried Gnosis partition is published in the companion Hugging Face Dataset.
