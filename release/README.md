# Cross-repository RC26 release contract

`release_contract.json` coordinates the scientific-source, data, code, paper,
and read-only demo revisions. `reproduction_config.json` is the executable
offline Dataset-to-results contract. The visual workflow is generated from
`docs/open-science-pipeline/pipeline_manifest.json`, not maintained by hand.

Validate it with:

```bash
python scripts/validate_release_contract.py
```

The validator checks structure, immutable commit syntax, repository URLs, evidence-boundary presence, and the rule that a release with unresolved blockers cannot claim `READY`. It does not replace clean-room pipeline execution, LaTeX compilation, official Croissant validation, or human scientific review.

## Reviewer reproduction assets

- `reproduction_config.json` pins the current Dataset, scientific-truth,
  paper, and Space candidate commits while leaving the human-approved final
  lock unset.
- `reference_results.json` is the deterministic result snapshot computed by
  `scripts/reproduce_release.py`.
- `reference_results.sha256` verifies the committed snapshot.

Run:

```bash
python scripts/validate_release_contract.py
python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all
(cd release && sha256sum -c reference_results.sha256)
```

The expected result hash is stored in `reference_results.sha256` and is
regenerated only after the pinned Dataset candidate passes its complete
checksum inventory.
This gate verifies the staged Dataset-to-results transformation. It does not
claim a new raw-chain extraction, a full empirical clean-room rerun, or a final
cross-repository release.
