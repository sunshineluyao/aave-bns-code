# NeurIPS 2026 code and reproducibility audit — 2026-08-20

## Primary guidance checked

- NeurIPS 2026 Main Track Handbook:
  https://neurips.cc/Conferences/2026/MainTrackHandbook
- NeurIPS 2026 Reviewer Guidelines:
  https://neurips.cc/Conferences/2026/ReviewerGuidelines
- Papers with Code research-code release checklist:
  https://github.com/paperswithcode/releasing-research-code

## Finding

`READY_FOR_PUBLIC_REVIEW` for the public code product and its pinned staged data
candidate. The default data-to-results workflow is credential-free, offline,
fail-closed, deterministic, and byte-compared with a committed reference. MIT
licensing, dependency declarations, exact reviewer versions, commands,
resources, seeds, outputs, evidence limitations, tests, and result-level
traceability are explicit.

## Checklist

| Requirement | Status | Evidence |
|---|---|---|
| Installation and dependencies | Pass | `pyproject.toml`, `requirements-release.lock` |
| Evaluation/reproduction code | Pass | `scripts/reproduce_release.py` |
| Exact public input | Pass | data commit pinned in `release/reproduction_config.json` |
| Result table and commands | Pass | README R01–R11 table and `release/result_replication_index.json` |
| Expected output | Pass | `release/reference_results.json` plus SHA-256 |
| Automated tests | Pass | 117 portable tests; 91 visible conditional historical-integration skips |
| Lint | Pass | Ruff over `src`, `tests`, and `scripts` |
| Continuous integration | Pass locally | Python 3.11/3.12 workflow committed; remote run required after push |
| Training code | Not applicable | no model is trained |
| Pretrained model | Not applicable | no model or weights exist |
| Hardware and runtime | Pass | documented in `docs/RELEASE_REPRODUCIBILITY.md` |
| Scientific limitations | Pass | six evidence boundaries in the release contract |

The 91 conditional tests depend on separately governed historical integration
or publication-layout artifacts that are not part of the public code package.
They remain collected and visibly skipped by default; setting
`AAVE_BNS_RUN_EXTERNAL_ASSET_TESTS=1` opts into them only when all required
inputs are staged. This does not weaken the credential-free reviewer path,
which has its own full data checksum, schema, result-index, reference, negative
smoke, and unit-test gates.

## Remaining external gate

The code review path is complete. Dataset publication itself still requires an
authenticated Hugging Face upload followed by immutable-revision loading,
Dataset Viewer inspection, and validation of the merged Hub Croissant RAI/PROV
artifact. The code contract does not claim those post-upload checks have run.
