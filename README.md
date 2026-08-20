# Aave-BNS reproducible analysis code

This MIT-licensed repository contains the executable acquisition, transformation,
simulation, network-analysis, diagnostic, and release-validation code for the
Aave-BNS study. Public release inputs are provided by
[`sunshineluyao/aave-bns-data-HF`](https://github.com/sunshineluyao/aave-bns-data-HF).
The release path is intentionally limited to the public data and code products.

## Reproduce the public result snapshot

Clone the data and code repositories side by side, pin the validated data
candidate, and run the fail-closed reviewer path:

```bash
git clone https://github.com/sunshineluyao/aave-bns-data-HF.git
git -C aave-bns-data-HF checkout 942b7c1b63c9b3deb1732e970b9a727a8a3a349a

git clone https://github.com/sunshineluyao/aave-bns-code.git
cd aave-bns-code
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-release.lock

make reproduce-release DATASET_ROOT=../aave-bns-data-HF
make result-index-validate DATASET_ROOT=../aave-bns-data-HF
(cd release && sha256sum -c reference_results.sha256)
```

The release command verifies the complete data checksum inventory, validates all
14 Hugging Face configurations plus both metadata-only evidence-gap tables,
recomputes the derived result snapshot, and compares it byte-for-byte with
`release/reference_results.json`. It runs offline and does not query a chain,
provider, or API.

## Result-by-result replication map

The machine-readable source for this table is
`release/result_replication_index.json`; the validator cross-checks it against
`metadata/result_data_crosswalk.csv` in the data repository. “Direct” means the
reported table is itself a governed, checksummed result asset; “computed” means
the release harness recalculates a compact summary from that asset.

| ID | Reported result or boundary | Public data assets | Code path and reproduced output |
|---|---|---|---|
| R01 | Audited weekly protocol-action counts | `observed_protocol_action_counts` | `scripts/reproduce_release.py`; direct checksum/schema/row validation in `results.inventory` |
| R02 | Governed event dates and timing rules | `treatment_event_registry` | `scripts/reproduce_release.py`; direct validation in `results.inventory` |
| R03 | Weekly address participation and event-frequency concentration | `participation_and_concentration_metrics` | computed as `results.event_totals` and `results.weekly_beneficiary_hhi` |
| R04 | Weekly Ethereum–Arbitrum address overlap | `cross_chain_overlap` | `scripts/reproduce_release.py`; direct validation in `results.inventory` |
| R05 | Chain-layer topology and concentration metrics | `structural_metrics` | computed as `results.all_actions_structural_snapshot` |
| R06 | Economic-actor concentration bounds and change envelope | `actor_bounds_period`, `actor_bounds_change` | computed as `results.economic_actor_hhi_change_bound`; signed actor conclusion must remain false |
| R07 | Synthetic network-formation scenarios | `simulation` | direct release validation; regenerate with `PYTHONPATH=src python -m aave_bns.cli simulate` |
| R08 | Arbitrum–Gnosis diagnostic, event study, placebos, and pretrends | four `failed_design_*` configurations | horizon-16 summary in `results.failed_design_horizon_16`; all four tables validated and retained as `FAILED_DESIGN` |
| R09 | Governed event-source verification | `source_audit` | `scripts/reproduce_release.py`; direct validation in `results.inventory` |
| R10 | Metric formulas, units, and supported interpretations | `metric_registry` | `scripts/reproduce_release.py`; direct validation in `results.inventory` |
| R11 | Route-level infrastructure evidence is unavailable | metadata-only `infrastructure_evidence_status` | negative replication gate: the table must remain excluded from Hub configs and the claim remains `BLOCKED` |

Every row has an exact command, field list, evidence status, output locator, and
interpretation boundary in `release/result_replication_index.json`.

## Reviewer and CI checks

```bash
make release-smoke
make release-contract
make result-index-validate DATASET_ROOT=../aave-bns-data-HF
make test
make lint
make pipeline-validate DATASET_ROOT=../aave-bns-data-HF
```

The smoke fixture is synthetic and self-contained. The full reviewer command
uses the pinned public data candidate. Continuous integration runs linting,
unit tests, contract validation, the smoke test, and schema-only result-index
validation on CPython 3.11 and 3.12.

In a clean public checkout, 117 portable tests pass. Another 91 historical
integration tests are collected but skipped because their separately governed
large or publication-layout artifacts are not distributed in this code
package. They run only when those inputs are deliberately staged and
`AAVE_BNS_RUN_EXTERNAL_ASSET_TESTS=1` is set; the skip is visible, never silent.

## Evidence boundaries

- Address identifiers are not verified people or independent economic actors.
- Pool-event-frequency HHI is not capital, liquidity, ownership, risk, welfare,
  or governance power.
- Weekly-mean and pooled-period HHI changes are different nonlinear
  aggregations and are not interchangeable.
- Simulation outputs are synthetic and uncalibrated.
- Arbitrum–Gnosis estimates are failed-design diagnostics, not treatment
  effects.
- Verified route-level infrastructure dependence remains blocked.

## Reproducibility scope

The default release command starts from the completed, immutable public data
candidate. Acquisition modules and queries are retained for independent future
refreshes, but they require explicit credentials and provider configuration and
never run implicitly. Missing or mismatched pinned data fail closed.

No training procedure or pretrained model exists for this study; those common
machine-learning release items are not applicable. Evaluation consists of
deterministic empirical transformations, diagnostics, checksum verification,
and exact reference comparison. Hardware, runtime, environment, seeding,
expected outputs, and limitations are documented in
`docs/RELEASE_REPRODUCIBILITY.md`; the dated NeurIPS/Papers with Code audit is
in `docs/NEURIPS_2026_CODE_READINESS.md`.

## Repository map

```text
analysis/                     reference statistical implementation
configs/                      chain, source, contract, and analysis settings
queries/                      source-specific acquisition queries
src/aave_bns/                 acquisition, transformation, network, and analysis code
scripts/reproduce_release.py  offline data-to-results release gate
release/                      public pins, result index, reference, and contract
docs/open-science-pipeline/   generated editable pipeline and registry
tests/                        unit, integration, and synthetic release fixtures
```

## License

Code is released under the [MIT License](LICENSE). The companion data release
uses CC BY 4.0 International; its license and third-party-rights boundary are
documented in the data repository.
