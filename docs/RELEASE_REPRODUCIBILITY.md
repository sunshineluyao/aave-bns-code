# Public data-to-results reproducibility guide

## Scope and evidence ceiling

This guide provides a credential-free path from the public staged dataset to a
deterministic result snapshot. It demonstrates computational reproducibility:
the same data, code, conditions, and governed transformations produce the same
bytes. It does not claim an independent replication with newly collected data,
prove causal identification, or remove upstream measurement limitations.

The default path does not execute blockchain queries. Completed acquisition
evidence is preserved in the public data repository. The acquisition modules in
this repository are retained for a separately authorized refresh and never run
when a pinned input is missing.

## Public inputs

| Product | Repository | Candidate revision | License |
|---|---|---|---|
| Data | `sunshineluyao/aave-bns-data-HF` | `942b7c1b63c9b3deb1732e970b9a727a8a3a349a` | CC BY 4.0 International |
| Code | `sunshineluyao/aave-bns-code` | reviewed commit or release tag | MIT |

The release contract depends only on these public data and code products. The
data candidate contains 14 Hub configurations, two metadata-only evidence-gap
tables, schemas, source and claim ledgers, a migration manifest, and a complete
SHA-256 inventory.

## Tested environment and resources

| Item | Reviewer path |
|---|---|
| Python | CPython 3.11 or 3.12; package minimum is 3.10 |
| Dependencies | exact versions in `requirements-release.lock` |
| Hardware | CPU only; no GPU or accelerator |
| Memory | under 1 GB for the compact release path |
| Disk | under 250 MB including a virtual environment and outputs |
| Network | needed only to clone/install; disabled for reproduction itself |
| Secrets | none |
| Randomness | no randomness in the release harness; simulation seed is fixed in `configs/simulation.yaml` |
| Typical runtime | seconds for the release harness; minutes for the full test suite on a laptop-class CPU |

## Exact reviewer commands

```bash
git clone https://github.com/sunshineluyao/aave-bns-data-HF.git
git -C aave-bns-data-HF checkout 942b7c1b63c9b3deb1732e970b9a727a8a3a349a

git clone https://github.com/sunshineluyao/aave-bns-code.git
cd aave-bns-code
git checkout <reviewed-code-commit-or-release-tag>

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-release.lock

python scripts/validate_release_contract.py
python scripts/validate_result_replication_index.py \
  --dataset-root ../aave-bns-data-HF
python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all \
  --output-dir outputs/release_review
(cd release && sha256sum -c reference_results.sha256)
```

Generated files:

- `outputs/release_review/results.json`;
- `outputs/release_review/SHA256SUMS.txt`.

The first file must equal `release/reference_results.json` exactly. Its expected
digest is committed in `release/reference_results.sha256`.

## Result coverage

`release/result_replication_index.json` is the authoritative result-level map.
It is mirrored as a readable table in the root README and cross-checked against
`metadata/result_data_crosswalk.csv` in the data repository. It covers R01–R11:
activity, events, participation/concentration, cross-chain overlap, topology,
actor bounds, simulation, failed-design diagnostics, source verification,
metric definitions, and the blocked infrastructure-evidence boundary.

Before calculating a result, the harness:

1. rejects unsafe or duplicate checksum paths;
2. hashes every file listed in the data checksum manifest;
3. checks all 14 configuration names, schemas, row counts, primary evidence
   states, and two non-loadable evidence gaps;
4. recomputes the compact result families using decimal-stable operations;
5. rejects any comparative diagnostic not marked `diagnostic_not_causal`;
6. rejects actor bounds that permit an unsupported signed conclusion; and
7. compares the complete canonical JSON result with the committed reference.

## Test and quality gates

```bash
make release-smoke
python -m pytest -q
ruff check src tests scripts
make pipeline-validate DATASET_ROOT=../aave-bns-data-HF
```

The smoke fixture is synthetic and tests a successful deterministic run,
checksum rejection after mutation, and rejection of an invalid causal
promotion even after the fixture is re-checksummed. Unit tests cover
acquisition boundaries, normalization, simulations, network measures,
descriptive analysis, partial identification, diagnostics, contracts, and
release reproduction. The clean public checkout passes 117 portable tests and
visibly skips 91 historical integration tests whose separately governed large
or publication-layout artifacts are absent. Those tests run only with the
required inputs and `AAVE_BNS_RUN_EXTERNAL_ASSET_TESTS=1`. CI repeats linting,
portable tests, contract checks, and the smoke gate on Python 3.11 and 3.12.

## NeurIPS and Papers with Code checklist

The release is organized around the NeurIPS 2026 quality and clarity criteria:
claims are paired with evidence-state boundaries, and a technically qualified
reviewer receives enough environment, data, command, and expected-output detail
to reproduce the computational results. The code-release checklist is handled
as follows:

| Checklist item | Implementation |
|---|---|
| Dependencies | `pyproject.toml` plus exact reviewer lock in `requirements-release.lock` |
| Training code | Not applicable: the study trains no model |
| Evaluation code | `scripts/reproduce_release.py`, result index, exact reference, and tests |
| Pretrained models | Not applicable: no model or weights are used |
| README results and commands | R01–R11 replication table with one-command release path |

Primary guidance used for this audit:

- NeurIPS 2026 Main Track Handbook:
  https://neurips.cc/Conferences/2026/MainTrackHandbook
- NeurIPS 2026 Reviewer Guidelines:
  https://neurips.cc/Conferences/2026/ReviewerGuidelines
- Papers with Code research-code release checklist:
  https://github.com/paperswithcode/releasing-research-code

## Interpretation and operational boundaries

- Addresses are not verified people or independent economic actors.
- Event-frequency HHI is not capital, liquidity, wealth, risk, welfare,
  ownership, or governance power.
- Weekly-mean and pooled-period HHI changes are not interchangeable.
- Simulation output is synthetic and uncalibrated.
- Comparative estimates are retained as failed-design diagnostics, not effects.
- Route-level infrastructure claims remain blocked.
- Raw acquisition can be provider-, time-, and credential-dependent and is not
  part of the compact reviewer command.

## Troubleshooting

- **Checksum mismatch:** restore the exact data candidate and remove local data
  edits; never overwrite the reference to force a pass.
- **Schema or configuration mismatch:** verify the data commit above and ensure
  both repositories are siblings.
- **Reference mismatch:** retain the generated JSON and report the first path
  printed by the comparator.
- **Missing optional system tools:** Inkscape is required only to re-export the
  pipeline PDF/PNG, not to reproduce numeric results.
- **Need a fresh acquisition:** follow the explicit real-version workflow and
  record a new governed snapshot; do not substitute it into this release.
