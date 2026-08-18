# RC8 Dataset-to-results reproduction guide

## Scope and evidence ceiling

This guide gives an interdisciplinary reviewer one deterministic path from the
governed RC8 Dataset package to the compact results consumed by the paper and
demo. It uses the NASEM meaning of **computational reproducibility**: the same
data, code, methods, and conditions. It does not claim independent replication
with new data.

Two workflows are deliberately separated:

1. **Released Dataset to reviewer-facing results** — credential-free after the
   two private repositories have been cloned. This is the release gate
   implemented here.
2. **Raw public-chain acquisition to released Dataset** — RPC/provider,
   time-, and compute-dependent. Existing `real_v2`–`real_v6` scripts and
   verification targets document it, but that full workflow was not executed
   during this PR update.

Passing the first workflow shows that the staged Dataset package is internally
consistent with the committed result snapshot. It does not prove that the
scientific claims are true, causal, generalizable, or free from upstream data
errors.

## Immutable candidate inputs

| Product | Repository | Candidate revision |
|---|---|---|
| Scientific truth | `sunshineluyao/aave-bns` PR #29 | `932f6f4f62c3402adf38231ed83ea9ca17cc227c` |
| Dataset | `sunshineluyao/aave-bns-data-HF` PR #1 | `33e4077acaa0cad82930f5c76cc05d9594ad51ef` |
| Paper | `sunshineluyao/aave-bns-paper` PR #1 | `24d559b8f5f764f76590e1877f799c1244ad57b4` |
| Space source | `sunshineluyao/aave-bns-demo-HF` PR #1 | `de702cffb5307b21fdc03c692775678ac492581d` |

These are inspectable candidate commits, not a final cross-product release
lock. The final Dataset license, Hugging Face publication revision, code
revision, paper revision, Space revision, and release approval remain explicit
human decisions. `release/reproduction_config.json` therefore keeps
`locked_revision` and `final_cross_repository_lock` as `null`.

## Prerequisites

- Git with credentials for the private repositories.
- CPython 3.11.11 (the pure-standard-library release entry point also works on
  supported Python 3.10+).
- Approximately 100 MB of free disk for source, the compact Dataset, the
  environment, and generated JSON.
- No RPC secret, Google Cloud project, GPU, or paid API is required for this
  Dataset-to-results workflow.

The scientific pipeline's exact reviewer environment is recorded in
`requirements-release.lock`. The release entry point itself intentionally
uses only Python's standard library so that package validation can run before
third-party installation.

## Exact commands

```bash
# 1. Obtain the immutable candidate Dataset.
git clone https://github.com/sunshineluyao/aave-bns-data-HF.git
git -C aave-bns-data-HF checkout 33e4077acaa0cad82930f5c76cc05d9594ad51ef

# 2. Obtain this code PR and check out its reported final commit.
git clone https://github.com/sunshineluyao/aave-bns-code.git
cd aave-bns-code
# Replace CODE_PR_FINAL_SHA with the immutable SHA reported on PR #1.
git checkout CODE_PR_FINAL_SHA

# 3. Create the pinned environment.
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-release.lock

# 4. Verify the complete package, recompute results, and compare the reference.
python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all \
  --output-dir outputs/release_review

# 5. Independently check the committed reference file.
(cd release && sha256sum -c reference_results.sha256)
```

The command must end with:

```text
PASS: canonical package verified; deterministic result snapshot sha256=5bc0b12ecccf02cfedaeca96f88c008477f1a9aedd06772bcb4d67ff41979b65
NOTICE: candidate dataset commit is immutable, but the final cross-repository lock remains a human release decision.
```

Generated files:

- `outputs/release_review/results.json`
- `outputs/release_review/SHA256SUMS.txt`

## What is recomputed

| Output family | Canonical Dataset config | Deterministic operation | Evidence state |
|---|---|---|---|
| Chain event totals | `participation_and_concentration_metrics` | Sum weekly Pool-event counts by chain | `DERIVED` |
| Weekly concentration change | `participation_and_concentration_metrics` | Mean beneficiary HHI for weeks −16…−1 and 1…16; week 0 excluded; report relative change | `DERIVED` |
| Role-network snapshot | `structural_metrics` | Select `all_actions` layer and preserve topology/centrality measures | `DERIVED` |
| Arbitrum–Gnosis diagnostic | `failed_design_estimates` | Select the 16-week horizon and preserve estimate, Newey–West SE, and interval | `FAILED_DESIGN` |
| Economic-actor HHI bound | `actor_bounds_change` | Select the evidence-assumption outer bound and enforce no signed actor conclusion | `BOUNDED` |

Before any transformation, the entry point:

1. rejects unsafe or duplicate checksum paths;
2. verifies either every checksum entry (release gate) or only required fixture
   inputs (smoke development);
3. requires the exact 16-config inventory;
4. matches every config's CSV header and row count to
   `metadata/release_manifest.json`;
5. matches all evidence states;
6. refuses a failed-design row not marked `diagnostic_not_causal`; and
7. refuses actor bounds that permit an economic-actor conclusion.

## Offline smoke and reference fixture

```bash
make release-smoke
python -m pytest -q tests/test_release_reproduction.py tests/test_release_contract.py
```

`tests/fixtures/release_minimal/` is synthetic test material constructed only
to exercise the release interface. Its values are not empirical evidence and
must never be cited. The tests verify a successful deterministic run, checksum
failure after mutation, and rejection of causal promotion after a logically
invalid fixture is re-checksummed.

## Evidence and interpretation boundaries

- Addresses are protocol identifiers, not verified people or independent
  economic actors.
- Pool-event-frequency HHI is not capital, liquidity, ownership, risk, welfare,
  or governance power.
- Mean-weekly HHI change and pooled-period HHI change are different nonlinear
  aggregations and are not interchangeable.
- Simulations are synthetic and uncalibrated.
- Arbitrum–Gnosis outputs are failed-design diagnostics, not treatment effects.
- Verified route-level infrastructure dependence remains blocked.
- `BLOCKED` and `PLANNED` Dataset configurations are inventoried but never
  promoted into computed results.

## Raw-chain workflow boundary

The credentialed acquisition and full rebuild remain separately available:

```bash
make reproduce-real-v2
make reproduce-real-v3
make reproduce-real-v4
make reproduce-real-v5-candidate
```

Those commands can incur provider cost and material runtime and require the RPC
variables documented by their respective guides. They were not run in this PR
update. Reviewers should not treat the harness, fixture tests, or Dataset
checksum comparison as evidence that a new raw-chain extraction succeeded.

## Troubleshooting

- **Checksum mismatch:** ensure the Dataset is checked out at the exact
  candidate revision, has no modified files, and was not exported through a
  tool that normalized line endings.
- **Config or schema mismatch:** the Dataset and code commits are from
  different release candidates; check both SHAs above.
- **Reference mismatch:** retain generated output, do not overwrite the
  reference, and open an issue identifying the first JSON path reported.
- **Private-repository access:** request access from the corresponding author;
  do not substitute an unversioned download.
- **Final lock remains null:** expected for this provisional PR. Only the human
  release owner may approve and record the final cross-repository lock.

## Audit status

This PR update reaches evidence level E4 for the committed synthetic smoke
fixture and deterministic transformation calculations over the inspected
candidate Dataset inputs. Complete byte-level verification of the private
Dataset clone and a raw-chain clean-room rerun remain tasks for an authenticated
reviewer/runner. Release recommendation: **not ready for final publication**,
but the code product is ready for reviewer inspection of the staged
Dataset-to-results pathway.
