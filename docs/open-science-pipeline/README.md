# RC26 open-science pipeline figure

The figure is generated from `pipeline_manifest.json`; the SVG is the editable
master and the PDF/PNG are deterministic exports. The supplied conceptual
infographics informed the visual density and four-part narrative, but no logo,
date, causal method, or evidence claim was copied from them.

Caption: **Aave-BNS RC26 open data science pipeline.** Completed public-chain
and governance acquisition in the pinned scientific-source repository is
migrated—without a new query—into separate queried-evidence and processed-table
layers. Hash, schema, and evidence-state checks gate offline analysis. Generated
results and the merged RC26 paper remain governed by a claim ledger that keeps
observed, derived, bounded, synthetic, failed-design, and blocked evidence
distinct. Dashed publication gates indicate unresolved licensing, reuse,
privacy, Hub, and platform validation requirements.

Regenerate with `make pipeline-figure`; validate the exact data/code contract
with `make pipeline-validate DATASET_ROOT=../aave-bns-data-HF`.

