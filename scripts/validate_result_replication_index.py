#!/usr/bin/env python3
"""Validate the public result-to-data-to-code replication index."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "release/result_replication_index.json"
EXPECTED_IDS = {f"R{number:02d}" for number in range(1, 12)}
EXPECTED_REPOSITORIES = {
    "data": "https://github.com/sunshineluyao/aave-bns-data-HF",
    "code": "https://github.com/sunshineluyao/aave-bns-code",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
NONPUBLIC_REPOSITORY_URL = re.compile(
    r"https://github\.com/[^\s\"']+/[^/\s\"']*(?:paper|manuscript)[^/\s\"']*",
    re.IGNORECASE,
)


def safe_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError(f"unsafe path: {relative}")
    path = root.joinpath(*posix.parts).resolve()
    path.relative_to(root.resolve())
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_inference_ledger(dataset_root: Path) -> list[str]:
    """Recompute R08 and compare every governed value, not merely its row count."""
    from generate_model_based_inference import generate

    chain_path = dataset_root / "data/processed/participation_and_concentration_metrics/data.csv"
    event_path = dataset_root / "data/processed/failed_design_event_study/data.csv"
    governed_path = dataset_root / "data/processed/model_based_inference/data.csv"
    generated = generate(chain_path, event_path)
    governed = pd.read_csv(governed_path)
    key = ["analysis_family", "outcome", "term", "specification"]
    errors: list[str] = []
    if list(generated.columns) != list(governed.columns):
        return ["R08 generated and governed ledger columns differ"]
    generated = generated.sort_values(key).reset_index(drop=True)
    governed = governed.sort_values(key).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            generated,
            governed,
            check_dtype=False,
            check_exact=False,
            rtol=1e-10,
            atol=1e-12,
        )
    except AssertionError as exc:
        errors.append(f"R08 generated ledger differs from governed ledger: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="validate code-side structure without requiring the companion data checkout",
    )
    args = parser.parse_args()
    errors: list[str] = []
    index = json.loads(INDEX.read_text(encoding="utf-8"))

    if index.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if index.get("public_repositories") != EXPECTED_REPOSITORIES:
        errors.append("public_repositories must contain exactly the public data and code repositories")
    if not SHA40.fullmatch(str(index.get("dataset_revision", ""))):
        errors.append("dataset_revision must be an immutable 40-character SHA")
    expected_manifest_sha256 = str(index.get("dataset_checksum_manifest_sha256", ""))
    if not SHA256.fullmatch(expected_manifest_sha256):
        errors.append("dataset_checksum_manifest_sha256 must be a 64-character SHA-256")
    common_command = str(index.get("common_release_command", ""))
    if "scripts/reproduce_release.py" not in common_command or "--dataset-root" not in common_command:
        errors.append("common_release_command must run the offline release entry point")

    rows = index.get("results") or []
    by_id = {row.get("result_id"): row for row in rows if isinstance(row, dict)}
    if len(rows) != len(by_id) or set(by_id) != EXPECTED_IDS:
        errors.append(f"result IDs must be exactly {sorted(EXPECTED_IDS)}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for result_id, row in sorted(by_id.items()):
        if result_id not in readme:
            errors.append(f"README replication table omits {result_id}")
        if not row.get("dataset_configurations") or not row.get("data_assets"):
            errors.append(f"{result_id} has no data mapping")
        if not row.get("code_entrypoints"):
            errors.append(f"{result_id} has no code entry point")
        if not row.get("output_locator") or not row.get("replication_mode"):
            errors.append(f"{result_id} has no reproducible output locator")
        if len(str(row.get("interpretation_boundary", "")).strip()) < 20:
            errors.append(f"{result_id} interpretation boundary is not substantive")
        for relative in row.get("code_entrypoints") or []:
            try:
                path = safe_path(ROOT, relative)
            except ValueError as exc:
                errors.append(f"{result_id}: {exc}")
            else:
                if not path.is_file():
                    errors.append(f"{result_id} code entry point is missing: {relative}")

    if not args.schema_only:
        if args.dataset_root is None:
            errors.append("--dataset-root is required unless --schema-only is used")
        else:
            dataset_root = args.dataset_root.resolve()
            checksum_manifest = dataset_root / "metadata/checksums.sha256"
            if not checksum_manifest.is_file():
                errors.append("dataset checksum manifest is missing")
            elif sha256_file(checksum_manifest) != expected_manifest_sha256:
                errors.append(
                    "dataset checkout does not match the pinned checksum-manifest receipt "
                    f"{expected_manifest_sha256}"
                )
            manifest = json.loads(
                (dataset_root / "metadata/release_manifest.json").read_text(encoding="utf-8")
            )
            config_names = {item["name"] for item in manifest.get("configs", [])}
            gap_names = {
                item["name"] for item in manifest.get("metadata_only_evidence_gaps", [])
            }
            available = config_names | gap_names
            crosswalk_path = dataset_root / "metadata/result_data_crosswalk.csv"
            with crosswalk_path.open(newline="", encoding="utf-8-sig") as handle:
                crosswalk = {row["result_id"]: row for row in csv.DictReader(handle)}
            if set(crosswalk) != EXPECTED_IDS:
                errors.append("data crosswalk result IDs do not match the public result index")
            for result_id, row in sorted(by_id.items()):
                mapped = crosswalk.get(result_id)
                if not mapped:
                    continue
                expected_configs = mapped["dataset_configuration"].split(";")
                if row.get("dataset_configurations") != expected_configs:
                    errors.append(f"{result_id} configuration mapping differs from data crosswalk")
                expected_fields = mapped["dataset_fields"].split(";")
                if row.get("dataset_fields") != expected_fields:
                    errors.append(f"{result_id} field mapping differs from data crosswalk")
                for key in ("result_family", "public_result", "evidence_status", "interpretation_boundary"):
                    if row.get(key) != mapped[key]:
                        errors.append(f"{result_id}.{key} differs from data crosswalk")
                missing_configs = set(row.get("dataset_configurations") or []) - available
                if missing_configs:
                    errors.append(f"{result_id} references unknown configurations: {sorted(missing_configs)}")
                for relative in row.get("data_assets") or []:
                    try:
                        path = safe_path(dataset_root, relative)
                    except ValueError as exc:
                        errors.append(f"{result_id}: {exc}")
                    else:
                        if not path.is_file():
                            errors.append(f"{result_id} data asset is missing: {relative}")
            errors.extend(validate_inference_ledger(dataset_root))

    suffixes = {".md", ".json", ".csv", ".yml", ".yaml", ".txt", ".cff", ".svg"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if "__pycache__" in path.parts:
            continue
        match = NONPUBLIC_REPOSITORY_URL.search(
            path.read_text(encoding="utf-8", errors="ignore")
        )
        if match:
            errors.append(
                f"non-public repository URL in {path.relative_to(ROOT)}: {match.group(0)}"
            )

    report = {
        "status": "PASS" if not errors else "FAIL",
        "result_count": len(rows),
        "dataset_validation": "SKIPPED_SCHEMA_ONLY" if args.schema_only else "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
