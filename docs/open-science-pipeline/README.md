# Open-science pipeline figure

The figure is generated from `pipeline_manifest.json`; the SVG is the editable
master and the PDF/PNG are deterministic exports. The supplied conceptual
infographics informed the visual density and four-part narrative, but no logo,
date, causal method, or evidence claim was copied from them.

Caption: **Aave-BNS open data science pipeline.** Completed public-chain and
governance acquisition is migrated—without a new query—into separate queried
evidence and processed-table layers in the public data product. Hash, schema,
and evidence-state checks gate offline analysis in the public code product.
Generated outputs flow into an R01–R11 replication index that maps each result
or explicit evidence boundary to public data and executable code. The claim
ledger keeps observed, derived, bounded, synthetic, failed-design, and blocked
evidence distinct. Dashed publication gates denote the remaining Hugging Face
upload, Viewer, and post-upload Croissant checks.

Regenerate with `make pipeline-figure`; validate the exact data/code contract
with `make pipeline-validate DATASET_ROOT=../aave-bns-data-HF`.
