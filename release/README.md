# Public reproducibility release assets

- `release_contract.json` limits the public release boundary to the data and
  code products and records licenses, scientific safeguards, and post-upload
  dataset gates.
- `reproduction_config.json` is the executable offline data-to-results contract.
- `result_replication_index.json` maps every reported result or explicit
  evidence boundary (R01–R11) to public data, code, commands, and outputs.
- `reference_results.json` is the deterministic result snapshot.
- `reference_results.sha256` protects the committed snapshot.

Validate and reproduce with:

```bash
python scripts/validate_release_contract.py
python scripts/validate_result_replication_index.py \
  --dataset-root ../aave-bns-data-HF
python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all \
  --output-dir outputs/release_review
(cd release && sha256sum -c reference_results.sha256)
```

This gate verifies the staged public data-to-results transformation. It does
not claim a new raw-chain extraction, causal identification, or completion of
the Hugging Face post-upload platform checks.
