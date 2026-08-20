# Aave-BNS code: RC26 reproducible analysis

This repository owns the executable research code, offline release harness,
generated result snapshot, and editable open-science pipeline figure for the
RC26 Aave protocol-network study. It does not duplicate the paper or the
governed Dataset.

## Canonical inputs

| Product | Revision | Role |
|---|---|---|
| `sunshineluyao/aave-bns` | `932f6f4f62c3402adf38231ed83ea9ca17cc227c` | completed acquisition and governed scientific source; read-only |
| `sunshineluyao/aave-bns-data-HF` | `e4eb1a7007c82a3ba020be3432eaa04d98675a05` | migrated queried evidence, processed configurations, metadata, and checksums |
| `sunshineluyao/aave-bns-paper` | `8993caa628f0ff277f6f8e92c05bc8671d557ff1` | merged RC26 paper; independently validated 114-page Overleaf build; read-only |

No blockchain query is run in the RC26 release path. The original `real_v2`–
`real_v6` acquisition is preserved by revision, retrieval evidence, artifact
receipts, hashes, and row counts. Acquisition scripts remain available for a
future refresh or independent reproduction, but they require explicit data
roots and credentials and must never silently replace a missing pinned payload.

## Deterministic reviewer path

Clone the two active repositories side by side, check out the pinned Dataset
commit, and run:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-release.lock

python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all \
  --output-dir outputs/release_review

(cd release && sha256sum -c reference_results.sha256)
```

The entry point uses only Python’s standard library. It verifies the complete
Dataset checksum inventory; audits all 14 loadable configurations and the two
non-loadable evidence-gap tables; recomputes the compact result families; and
compares the output byte-for-byte with `release/reference_results.json`.

Run the self-contained synthetic smoke fixture without network access:

```bash
make release-smoke
```

## Evidence boundaries

- Address identifiers are not verified people or independent economic actors.
- Pool-event-frequency HHI is not capital, liquidity, ownership, risk, welfare,
  or governance power.
- Weekly-mean and pooled-period HHI changes are different nonlinear
  aggregations and are not interchangeable.
- Simulation outputs are synthetic and uncalibrated.
- Arbitrum–Gnosis estimates are `FAILED_DESIGN` diagnostics, not treatment
  effects.
- Verified route-level infrastructure dependence remains blocked.

## Open-science pipeline figure

The attached infographic references are treated as design inspiration only.
The implemented figure is evidence-driven and generated from the repository
registry:

```bash
make pipeline-figure
make pipeline-validate DATASET_ROOT=../aave-bns-data-HF
```

Outputs:

- `docs/open-science-pipeline/open_science_pipeline.svg` — editable master;
- `docs/open-science-pipeline/open_science_pipeline.pdf` — publication asset;
- `docs/open-science-pipeline/open_science_pipeline.png` — preview;
- `docs/open-science-pipeline/figure_manifest.json` — semantic graphics,
  palette, and provenance;
- `docs/open-science-pipeline/pipeline_manifest.json` — exact stage and edge
  contract.

## Repository map

```text
analysis/                     reference statistical implementation
configs/                      chain, source, contract, and analysis settings
queries/                      source-specific acquisition queries
src/aave_bns/                 acquisition, transformation, network, and analysis code
scripts/reproduce_release.py  offline Dataset-to-results release gate
release/                      pins, deterministic reference, and release contract
docs/open-science-pipeline/   generated vector pipeline and machine-readable registry
tests/                        unit, integration, and synthetic release fixtures
```

The public Dataset release remains `NOT_READY` until the authors approve a
license, complete upstream reuse and row-level privacy review, publish an
immutable Hugging Face revision, and pass Dataset Viewer/Croissant validation.
No license selection, visibility change, Hub upload, tag, or merge is performed
by this PR.
