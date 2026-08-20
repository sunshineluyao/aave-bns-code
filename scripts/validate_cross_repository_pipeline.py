#!/usr/bin/env python3
"""Fail-closed audit of the RC26 data/code pipeline and generated assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = "932f6f4f62c3402adf38231ed83ea9ca17cc227c"
DATA_REVISION = "e4eb1a7007c82a3ba020be3432eaa04d98675a05"
PAPER_REVISION = "8993caa628f0ff277f6f8e92c05bc8671d557ff1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "docs/open-science-pipeline/pipeline_manifest.json",
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    dataset_root = args.dataset_root.resolve()
    errors: list[dict[str, object]] = []

    def error(code: str, detail: object) -> None:
        errors.append({"code": code, "detail": detail})

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reproduction = json.loads((ROOT / "release/reproduction_config.json").read_text(encoding="utf-8"))
    release = json.loads((dataset_root / "metadata/release_manifest.json").read_text(encoding="utf-8"))
    pipeline_contract = json.loads((dataset_root / "metadata/pipeline_contract.json").read_text(encoding="utf-8"))

    revisions = manifest.get("revisions", {})
    expected = {
        "scientific_source": SOURCE_REVISION,
        "data_candidate": DATA_REVISION,
        "paper": PAPER_REVISION,
    }
    if revisions != expected:
        error("pins.pipeline_manifest", {"expected": expected, "actual": revisions})
    if reproduction.get("dataset", {}).get("candidate_revision") != DATA_REVISION:
        error("pins.reproduction_dataset", reproduction.get("dataset", {}).get("candidate_revision"))
    candidates = reproduction.get("cross_repository_candidates", {})
    if candidates.get("scientific_truth") != SOURCE_REVISION:
        error("pins.reproduction_source", candidates.get("scientific_truth"))
    if candidates.get("paper") != PAPER_REVISION:
        error("pins.reproduction_paper", candidates.get("paper"))
    if release.get("canonical_source_revision") != SOURCE_REVISION:
        error("pins.dataset_source", release.get("canonical_source_revision"))
    if release.get("paper_revision") != PAPER_REVISION:
        error("pins.dataset_paper", release.get("paper_revision"))
    if pipeline_contract.get("no_requery_policy") is not True or manifest.get("no_requery_for_rc26") is not True:
        error("policy.no_requery", "both data and figure contracts must fail closed")

    stages = manifest.get("stages", [])
    stage_ids = [item.get("id") for item in stages]
    if len(stages) != 8 or len(stage_ids) != len(set(stage_ids)):
        error("dag.stages", stage_ids)
    if sorted(item.get("order") for item in stages) != list(range(1, 9)):
        error("dag.order", [item.get("order") for item in stages])
    by_id = {item.get("id"): item for item in stages}
    for edge in manifest.get("edges", []):
        if edge.get("from") not in by_id or edge.get("to") not in by_id:
            error("dag.edge_endpoint", edge)
    for item in stages:
        revision = item.get("revision")
        if revision not in (None, "CURRENT_PR_HEAD") and not SHA40.fullmatch(str(revision)):
            error("dag.revision", {"stage": item.get("id"), "revision": revision})
        repo = str(item.get("repository") or "")
        roots: list[Path] = []
        if repo.endswith("/aave-bns-data-HF"):
            roots = [dataset_root]
        elif repo.endswith("/aave-bns-code"):
            roots = [ROOT]
        for rel in item.get("paths", []):
            if roots and not (roots[0] / rel).exists():
                error("dag.missing_path", {"stage": item.get("id"), "path": rel})

    with (dataset_root / "metadata/migration_manifest.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        migrations = list(csv.DictReader(handle))
    if len(migrations) != 32:
        error("migration.record_count", len(migrations))
    for row in migrations:
        target = row.get("target_path", "")
        if row.get("migration_status") == "VERIFIED_COPY":
            path = dataset_root / target
            if not path.is_file():
                error("migration.missing_target", target)
            elif sha256(path) != row.get("sha256"):
                error("migration.digest", row.get("artifact_id"))
        elif target:
            error("migration.unverified_target", row.get("artifact_id"))

    configs = release.get("configs", [])
    if len(configs) != 14:
        error("dataset.config_count", len(configs))
    for item in configs:
        if not str(item.get("path", "")).startswith("data/processed/"):
            error("dataset.processed_root", item.get("path"))
    queried = [path for path in (dataset_root / "data/queried").glob("*/*") if path.is_file()]
    if len(queried) != 14:
        error("dataset.queried_file_count", len(queried))

    required_assets = [
        ROOT / "docs/open-science-pipeline/open_science_pipeline.svg",
        ROOT / "docs/open-science-pipeline/open_science_pipeline.pdf",
        ROOT / "docs/open-science-pipeline/open_science_pipeline.png",
        ROOT / "docs/open-science-pipeline/figure_manifest.json",
    ]
    for path in required_assets:
        if not path.is_file() or path.stat().st_size == 0:
            error("figure.missing_asset", path.relative_to(ROOT).as_posix())

    report = {
        "status": "PASS" if not errors else "FAIL",
        "release_family": manifest.get("release_family"),
        "pins": expected,
        "stage_count": len(stages),
        "migration_record_count": len(migrations),
        "queried_evidence_files": len(queried),
        "processed_configurations": len(configs),
        "errors": errors,
        "publication_status": "NOT_READY",
        "publication_gate_count": len(manifest.get("publication_gates", [])),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

