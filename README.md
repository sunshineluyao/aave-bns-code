# Aave BNS: reproducible protocol-network research

This is the standalone **code product** for the RC8 Aave protocol-network
study. It contains the transformations, metrics, diagnostics, renderers, and
tests used by the project. The governed Dataset, paper, and Hugging Face Space
are maintained in separate repositories; this repository records immutable
candidate revisions and refuses to call them a final release until the human
cross-repository lock is completed. The study is a measurement and
reproducibility case study; the available benchmark does not identify a causal
effect of GHO issuance or cross-chain expansion.

## RC8 reviewer reproduction path

The shortest evidence-preserving route starts from the canonical Dataset
package, verifies its checksum and schema manifests, recomputes the released
reviewer-facing summaries, and compares the result byte-for-byte with the
committed reference:

```bash
git clone https://github.com/sunshineluyao/aave-bns-data-HF.git
git -C aave-bns-data-HF checkout ec8340befe6ba98d053482a8d0efc25577d7a222

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-release.lock

python scripts/reproduce_release.py \
  --dataset-root ../aave-bns-data-HF \
  --checksum-scope all
```

Expected deterministic result hash:
`d6c6c06b33b419d5e4d29cc8b86f8bd21c8ad9ff99195de33f973a3b9360a862`.
The command verifies all checksum-listed Dataset files; checks all 14
Hugging Face configuration schemas plus the two audit-only blocked tables,
including row counts and evidence states; recomputes event
totals, weekly HHI changes, structural snapshots, failed-design diagnostics,
and actor bounds; and compares the JSON result to
`release/reference_results.json`. See
`docs/RELEASE_REPRODUCIBILITY.md` for the transformation ledger,
troubleshooting, evidence ceiling, and the separate credentialed raw-chain
workflow.

Run the self-contained fixture without network access:

```bash
make release-smoke
```

The smoke gate validates the harness, checksum rejection, reference comparison,
and failed-design language boundary. It is not a full empirical rerun.

## Research status

- The evidence-bounded manuscript has a six-section main-paper MVP, complete evidence-status appendices, an editable Figure 1 source, and generated real-data tables and figures.
- Section 3 contains a deliberately narrow analytical benchmark and deterministic
  mechanism checks. They organize measurement hypotheses but are not calibrated to the
  empirical case and do not identify historical mechanisms.
- `real_v1` completed an audited, descriptive Ethereum GHO pilot: 9,460 logs,
  1,228 addresses, and 2,914 directed edges over eight exact post-activation weeks.
- `real_v2` now contains the first symmetric Ethereum protocol-action panel: 118,806
  Aave V3 Pool events in 103,171 transactions, involving 15,762 addresses and 25 reserves
  over exact event weeks -16 through +16 around GHO activation.
- `real_v3-ethereum-v0.1.0` checks historical runtime code at all 15,762 participant
  addresses and freezes ten primary-source protocol/asset labels. Contracts are 7.2% of
  addresses but carry 38.1% of deduplicated event-address incidences; the ten official
  labels cover 21.9% of incidences.
- The repository preserves a 31-event evidence ledger, a 21-source acquisition catalog,
  six A+ governance-controlled treatment cohorts, and exact provider-checked block boundaries.
- The `real_v3` economic-actor coverage gate fails closed. Address/entity comparisons are
  sensitivity measurements, not person counts, primary entity results, or causal estimates.
- `real_v4-ethereum-v0.1.0` replaces forced entity resolution with transparent partial
  identification. The position-holder address HHI falls 37.0% from pre to post. The much
  wider economic-actor change envelope $[-0.996445, 0.994353]$ is a conservative outer
  bound rather than a sharp identified set, so no signed actor-level conclusion, primary
  entity result, or causal estimate is produced.
- `real_v5` contains audited symmetric Ethereum–Arbitrum 33-week panels and address-role topology. Mean weekly position-holder HHI declines 31.5% on Ethereum; the separately pooled real_v4 pre/post HHI declines 37.0%. These nonlinear aggregations are both reported with their definitions and are not interchangeable.
- The gated `real_v5` descriptive layer reports address-level entry, exit, retention,
  HHI, Gini, Top 1%/10%, Nakamoto-51, and chain-relative overlap. Infrastructure
  dependence, entity-level results, and causal estimates remain withheld until their
  respective evidence gates pass.
- The gated `real_v5` topology layer uses symmetric audited Pool-event fields to report
  action-side-to-position-holder role networks. It opens address-level structural measurement while
  keeping bridge-route, entity-level, and causal gates closed.
- `real_v6-gnosis-donor-mvp-v0.1.0` adds 81,814 audited Gnosis Pool events and a
  66-row common-calendar Arbitrum–Gnosis benchmark. Calendar-lag Newey–West summaries
  give +1.9833 for log participation and -0.01842 for HHI at +16 weeks. This is a failed
  donor diagnostic—not policy evidence—because there is only one donor, differential
  pre-trends are nonzero, and the contrasts are donor-driven; therefore
  `causal_estimate_produced=false`.
- Causal effects remain gated on additional same-semantic cohorts, not-yet-treated support,
  pre-trend and placebo diagnostics, and chain-level inference. New actor or route claims
  require primary-source evidence that genuinely tightens the current bounds.
- Local and continuous-integration checks retain the deterministic synthetic fixture and
  reject drift in source tables, treatment calendars, generated empirical reports, and
  manuscript appendices. The final anonymous release was compiled and audited locally
  because the repository's GitHub Actions quota was unavailable.

## Reproducibility contract

```text
public chain / governance sources
          ↓
versioned queries + retrieval manifests
          ↓
normalized transfers and protocol events
          ↓
address-level temporal networks + actor identified sets
          ↓
network metrics + gated causal-design inputs
          ↓
paper tables / figures / flattened Springer package
          ↓
Hugging Face dataset and Space explorer
```

Every generated result records the input path, SHA-256 hash, configuration, software version, and generation time. Human-coded policy events are stored in `data/metadata/treatment_registry.csv` with source URLs and verification status.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
# Add BigQuery support only when running the public-data adapter:
python -m pip install -e '.[query]'

make demo       # deterministic end-to-end smoke test
make simulation # deterministic Section 3 mechanism check
make test       # unit and integration tests
make release-smoke # standard-library release harness against committed fixture
make reproduce-release DATASET_ROOT=../aave-bns-data-HF # canonical Dataset -> results
make verify-real-v4 # offline verification of the partial-identification release
make reproduce-real-v5-candidate # requires protected ARBITRUM_RPC_URL
make real-v6-gnosis-assets # render compact audited benchmark assets, no RPC required
make paper      # regenerate policy assets and compile the modular manuscript
make submission # fetch the pinned template and compile the flattened Springer package
```

For the frozen anonymous release, use the deterministic build settings recorded in
`paper/RELEASE_GATE_AUDIT_2026-08-11.md`. Ready-to-upload artifacts and their hashes are:

```text
paper/release/DeFi_Decentralization_Anonymous_Submission.pdf
paper/release/DeFi_Decentralization_Anonymous_Overleaf.zip
paper/release/SHA256SUMS.txt
```

The main demo outputs are written to:

```text
outputs/metrics/network_metrics.csv
outputs/causal/did_results.json
outputs/manifest.json
paper/generated/tables/
paper/generated/figures/
```

The simulation additionally writes `outputs/simulation/` and an explicitly labelled,
non-empirical table to `paper/generated/tables/simulation_mechanism_check.tex`.

## Audited real-world checkpoint

- [Source and treatment audit](docs/SOURCE_AUDIT.md): all 31 public event URLs,
  causal roles, evidentiary grades, and all 21 acquisition sources.
- [Real-world data guide](docs/REAL_WORLD_DATA.md): evidence, limitations,
  provenance rules, and the `real_v1` recovery boundary.
- [Locked `real_v2` plan](docs/REAL_V2_ANALYSIS_PLAN.md): six treatment cohorts,
  primary outcomes, estimators, and hard-stop rules.
- [Ethereum extraction method](docs/REAL_V2_ETHEREUM.md): pinned contract and ABI,
  exact boundaries, raw-log retrieval, validation, and measurement rules.
- [From-zero reproduction guide](docs/REPRODUCE_REAL_V2.md): public RPC setup,
  one-command verification, full rerun, expected files, and troubleshooting.
- [Ethereum extraction results](docs/REAL_V2_ETHEREUM_RESULTS.md): generated counts,
  weekly ranges, canonical hash, and interpretation guardrails.
- [Contract-role and entity audit](docs/REAL_V3_ENTITY_LAYER.md): historical bytecode,
  versioned primary-source labels, address/entity sensitivity, and coverage hard stops.
- [Economic-actor partial identification](docs/REAL_V4_PARTIAL_IDENTIFICATION.md):
  position-holder addresses, transparent HHI bounds, exact treatment timing, and the
  machine-enforced rule that withholds entity and causal conclusions.
- [Ethereum–Arbitrum comparable-panel protocol](docs/REAL_V5_ETHEREUM_ARBITRUM.md):
  one-secret extraction, independent public-RPC samples, symmetric beneficiary measures,
  and explicit downstream gates.
- [Ethereum–Arbitrum descriptive results](docs/REAL_V5_DESCRIPTIVE_RESULTS.md): audited
  address-level measures, cross-chain overlap, and machine-readable hard stops.
- [Ethereum–Arbitrum topology results](docs/REAL_V5_TOPOLOGY_RESULTS.md): symmetric
  action-side-to-position-holder networks, centrality concentration, and maximum k-core evidence.
- [Arbitrum–Gnosis benchmark scope](docs/BENCHMARK_DID_SCOPE_2026-08-11.md): common-calendar
  donor construction, exact estimates, diagnostic limitations, and causal-language gate.
- `outputs/real_v6/artifact_provenance.json` identifies the successful workflow run,
  artifact ID, verified digest, preserved raw-data location, and compact Git exclusions.
- `data/metadata/event_source_audit.csv` and `source_catalog.csv` are canonical;
  GitHub-facing Markdown and the LaTeX appendix are generated from them.
- `data/metadata/real_v2_event_week_calendar.csv` contains 198 deterministic cohort-week
  records and is regenerated in CI to prevent silent treatment-window drift.

## `real_v2` Ethereum action panel

The public-RPC pipeline extracts canonical Aave V3 `Supply`, `Withdraw`, `Borrow`,
`Repay`, and `LiquidationCall` logs for the locked 33-week window:

```bash
PYTHONPATH=src python scripts/run_real_v2_ethereum.py
```

To verify the published checkpoint without making any RPC request:

```bash
make verify-real-v2
```

To retrieve the raw logs, rebuild the panels, and require a full local-data audit:

```bash
make reproduce-real-v2
```

After `real_v2` exists locally, reproduce and verify the historical contract/entity layer:

```bash
make reproduce-real-v3
make verify-real-v3

# Build the position-holder panel and assumption-indexed actor bounds.
make reproduce-real-v4
make verify-real-v4
```

The `real_v2` run is partitioned into at most 10,000-block chunks and resumes from deterministic
gzip JSON Lines. It validates chain ID, exact block boundaries, treatment-block Pool
bytecode, event topics, unique log keys, and four independent-provider log samples.
Compact audit outputs are committed under `outputs/real_v2/ethereum/`; raw partitions
and the decoded row-level event file are regenerated locally and identified by SHA-256
in the portable manifest rather than duplicated in Git.

## Full Ethereum query

The legacy Ethereum token-transfer adapter targets the public BigQuery table used by the original research lineage. A Google Cloud project is required for billing and query execution.

```bash
python -m pip install -e '.[query]'
gcloud auth application-default login
export GCP_PROJECT='your-project-id'
aave-bns query \
  --config configs/analysis.yaml \
  --output data/raw/ethereum_token_transfers.csv
```

The query is parameterized by dates and token addresses, and a retrieval manifest is written beside the output. Public datasets and schemas can change; `configs/sources.yaml` therefore records the expected table and verification date. For non-Ethereum deployments, `aave-bns rpc-logs` provides a generic `eth_getLogs` adapter driven by explicit contract, topic, and block-range configuration.

## Aave V3 protocol sources

Protocol-state extraction is anchored to pinned Aave contract registries and interfaces,
with raw consensus RPC logs as the authoritative historical records. Official Aave V3
Subgraphs are a pre-specified future independent reconciliation layer, not a source of any
currently reported result and not a substitute for treatment-block or log validation.
Every empirical pull records the source commit or endpoint, request range, retrieval time,
response hash, and independent-provider checks. A governance proposal is not coded as a
treatment until execution and observed protocol availability are verified.

## Paper organization

The working manuscript is modular:

- `paper/references.bib`: bibliography;
- `paper/figures/*.tex`: figure environments;
- `paper/tables/*.tex`: table environments;
- `paper/appendix/*.tex`: generated empirical and source-audit appendices;
- `paper/main.tex`: manuscript that inputs the modules.

Springer’s submission checker may require one main TeX source. `paper/scripts/flatten_latex.py` creates `paper/submission/main_flat.tex` while retaining the modular development source.
The paper workflow regenerates all figure assets and compiles both the modular manuscript
and the flattened package. The frozen anonymous PDF and Overleaf ZIP are committed under
`paper/release/` so this PR remains self-contained even while remote Actions are unavailable.

## Data governance

The repository does not treat blockchain addresses as natural persons. Actor links require
pinned primary-source evidence, confidence, validity intervals, versioning, and a correction
history; shared bytecode or behavioral similarity never establishes common ownership. When
public evidence is insufficient, the repository reports assumption-indexed bounds rather than
forcing an entity assignment. Raw chain records are queried from public sources; derived
releases minimize unnecessary duplication and preserve block/transaction provenance. See
`docs/DATA_GOVERNANCE.md` and `docs/PROVENANCE.md`.

## Repository map

```text
configs/                 chain, contract, source, and analysis configuration
queries/                 source-specific SQL
src/aave_bns/            query, transform, network, causal, and reporting code
data/metadata/           verified treatment and source registries
data/sample/             deterministic synthetic fixture for CI
outputs/real_v2/--real_v6/ compact audited empirical panels, bounds, and manifests
analysis/r/              staggered-DiD reference implementation
paper/                   Springer Nature manuscript and generated assets
hf_space/                interactive explorer scaffold
tests/                   metric and end-to-end reproducibility tests
.github/workflows/       Python CI and LaTeX compilation
```

## Citation

Citation metadata is in `CITATION.cff`. The repository is released under the MIT License; third-party Springer Nature template files retain their own license notices.

## Springer template provenance

The repository fetches the December 2024 Springer Nature journal-article package from the publisher's versioned download endpoint. The class and bibliography-style files are verified against pinned SHA-256 hashes before compilation, avoiding an untracked local dependency while keeping publisher-owned template files out of the repository history.
