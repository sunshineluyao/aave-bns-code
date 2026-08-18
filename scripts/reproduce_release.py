#!/usr/bin/env python3
"""Reproduce the reviewer-facing RC8 result snapshot from the dataset package.

This entry point is intentionally standard-library-only.  It verifies the
dataset package before computing results, preserves evidence-state labels, and
compares deterministic output with a committed reference.  It does not query
RPC endpoints, rebuild raw logs, or turn failed-design diagnostics into causal
estimates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
RESULT_EVIDENCE_STATES = {
    "OBSERVED",
    "DERIVED",
    "BOUNDED",
    "SYNTHETIC",
    "FAILED_DESIGN",
}


class ReleaseError(RuntimeError):
    """A fail-closed release validation error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"expected a JSON object: {path}")
    return value


def resolve_inside(root: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReleaseError(f"unsafe package path: {relative!r}")
    path = root.joinpath(*candidate.parts).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ReleaseError(f"package path escapes root: {relative!r}") from exc
    return path


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseError(f"cannot read checksum manifest {path}: {exc}") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not SHA256.fullmatch(parts[0]):
            raise ReleaseError(f"malformed checksum line {number}: {line!r}")
        relative = parts[1].lstrip("*")
        if relative in checksums:
            raise ReleaseError(f"duplicate checksum path: {relative}")
        # Validate path even before joining it to a package root.
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ReleaseError(f"unsafe checksum path: {relative!r}")
        checksums[relative] = parts[0]
    if not checksums:
        raise ReleaseError("checksum manifest is empty")
    return checksums


def verify_checksums(
    dataset_root: Path,
    checksums: dict[str, str],
    required_paths: set[str],
    scope: str,
) -> list[str]:
    if scope == "all":
        selected = set(checksums)
    elif scope == "required":
        selected = set(required_paths)
    else:
        raise ReleaseError(f"unsupported checksum scope: {scope}")
    missing_entries = selected - set(checksums)
    if missing_entries:
        raise ReleaseError(
            "required paths are absent from checksum manifest: "
            + ", ".join(sorted(missing_entries))
        )
    verified: list[str] = []
    for relative in sorted(selected):
        path = resolve_inside(dataset_root, relative)
        if not path.is_file():
            raise ReleaseError(f"checksummed file is missing: {relative}")
        actual = sha256_file(path)
        expected = checksums[relative]
        if actual != expected:
            raise ReleaseError(
                f"checksum mismatch for {relative}: expected {expected}, got {actual}"
            )
        verified.append(relative)
    return verified


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise ReleaseError(f"cannot read CSV {path}: {exc}") from exc
    if not fields:
        raise ReleaseError(f"CSV has no header: {path}")
    return fields, rows


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "1.0":
        raise ReleaseError("reproduction config schema_version must be 1.0")
    dataset = config.get("dataset")
    if not isinstance(dataset, dict):
        raise ReleaseError("reproduction config requires dataset object")
    if not SHA40.fullmatch(str(dataset.get("candidate_revision", ""))):
        raise ReleaseError("dataset.candidate_revision must be an immutable 40-character SHA")
    locked = dataset.get("locked_revision")
    if locked is not None and not SHA40.fullmatch(str(locked)):
        raise ReleaseError("dataset.locked_revision must be null or a 40-character SHA")
    refs = config.get("cross_repository_candidates")
    if not isinstance(refs, dict) or not refs:
        raise ReleaseError("cross_repository_candidates must be present")
    for name, revision in refs.items():
        if not SHA40.fullmatch(str(revision)):
            raise ReleaseError(f"cross_repository_candidates.{name} must be a commit SHA")
    boundaries = config.get("evidence_boundaries")
    if not isinstance(boundaries, dict) or len(boundaries) < 5:
        raise ReleaseError("five substantive evidence boundaries are required")
    for name, value in boundaries.items():
        if not isinstance(value, str) or len(value.strip()) < 20:
            raise ReleaseError(f"evidence boundary {name} is missing or non-substantive")
    required = config.get("required_configs")
    gaps = config.get("metadata_only_evidence_gaps", {})
    if not isinstance(required, dict) or not required:
        raise ReleaseError("required_configs must be a non-empty object")
    if not isinstance(gaps, dict):
        raise ReleaseError("metadata_only_evidence_gaps must be an object")
    if set(required) & set(gaps):
        raise ReleaseError("Hugging Face configs and metadata-only gaps must be disjoint")


def validate_dataset_manifest(
    dataset_root: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    expected_version = config["dataset"]["package_version"]
    if manifest.get("version") != expected_version:
        raise ReleaseError(
            f"dataset version mismatch: expected {expected_version}, got {manifest.get('version')}"
        )
    raw_configs = manifest.get("configs")
    if not isinstance(raw_configs, list):
        raise ReleaseError("dataset release manifest requires configs list")
    by_name: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for item in raw_configs:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ReleaseError("dataset config entries must be objects with names")
        name = item["name"]
        if name in by_name:
            raise ReleaseError(f"duplicate dataset config: {name}")
        by_name[name] = item
    required = config.get("required_configs")
    if not isinstance(required, dict) or set(by_name) != set(required):
        raise ReleaseError(
            "dataset config inventory mismatch: expected "
            f"{sorted(required or {})}, got {sorted(by_name)}"
        )
    for name in sorted(required):
        item = by_name[name]
        status = item.get("evidence_status")
        if status != required[name]:
            raise ReleaseError(
                f"{name} evidence state mismatch: expected {required[name]}, got {status}"
            )
        relative = item.get("path")
        if not isinstance(relative, str):
            raise ReleaseError(f"{name} has no data path")
        fields, rows = read_csv(resolve_inside(dataset_root, relative))
        expected_rows = item.get("rows")
        if expected_rows != len(rows):
            raise ReleaseError(
                f"{name} row count mismatch: manifest {expected_rows}, observed {len(rows)}"
            )
        expected_fields = item.get("fields")
        if expected_fields != fields:
            raise ReleaseError(f"{name} schema differs from release manifest")
        paths.add(relative)

    raw_gaps = manifest.get("metadata_only_evidence_gaps", [])
    expected_gaps = config.get("metadata_only_evidence_gaps", {})
    if not isinstance(raw_gaps, list):
        raise ReleaseError("dataset release manifest requires metadata_only_evidence_gaps list")
    gap_names = {item.get("name") for item in raw_gaps if isinstance(item, dict)}
    if len(gap_names) != len(raw_gaps) or gap_names != set(expected_gaps):
        raise ReleaseError(
            "metadata-only evidence-gap inventory mismatch: expected "
            f"{sorted(expected_gaps or {})}, got {sorted(str(name) for name in gap_names)}"
        )
    for item in raw_gaps:
        name = item["name"]
        if item.get("status") != expected_gaps[name]:
            raise ReleaseError(
                f"{name} evidence state mismatch: expected {expected_gaps[name]}, "
                f"got {item.get('status')}"
            )
        if item.get("exposed_as_hf_configuration") is not False:
            raise ReleaseError(f"{name} must not be exposed as a Hugging Face configuration")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise ReleaseError(f"{name} has no audit-table path")
        fields, rows = read_csv(resolve_inside(dataset_root, relative))
        by_name[name] = {
            **item,
            "rows": len(rows),
            "fields": fields,
            "evidence_status": item["status"],
        }
        paths.add(relative)
    return by_name, paths


def decimal_text(value: Decimal) -> str:
    """Portable 15-significant-digit decimal representation."""
    return format(value, ".15g")


def compute_results(
    dataset_root: Path,
    by_name: dict[str, dict[str, Any]],
    config: dict[str, Any],
    verified_paths: list[str],
    checksums: dict[str, str],
) -> dict[str, Any]:
    participation_path = resolve_inside(
        dataset_root, by_name["participation_and_concentration_metrics"]["path"]
    )
    _, participation = read_csv(participation_path)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in participation:
        grouped[row["chain"]].append(row)
    hhi_results = []
    event_totals = []
    for chain in sorted(grouped):
        rows = grouped[chain]
        total = sum(int(row["event_count"]) for row in rows)
        event_totals.append(
            {
                "chain": chain,
                "chain_id": rows[0]["chain_id"],
                "event_count": total,
                "evidence_status": "DERIVED",
                "observed_unit": rows[0]["observed_unit"],
            }
        )
        pre = [
            Decimal(row["beneficiary_hhi"])
            for row in rows
            if int(row["event_week"]) < 0
        ]
        post = [
            Decimal(row["beneficiary_hhi"])
            for row in rows
            if int(row["event_week"]) > 0
        ]
        if not pre or not post:
            raise ReleaseError(f"{chain} lacks symmetric pre/post HHI observations")
        pre_mean = sum(pre) / Decimal(len(pre))
        post_mean = sum(post) / Decimal(len(post))
        hhi_results.append(
            {
                "chain": chain,
                "pre_weeks": len(pre),
                "post_weeks": len(post),
                "week_zero_excluded": True,
                "pre_mean_beneficiary_hhi": decimal_text(pre_mean),
                "post_mean_beneficiary_hhi": decimal_text(post_mean),
                "relative_change": decimal_text(post_mean / pre_mean - Decimal(1)),
                "evidence_status": "DERIVED",
                "interpretation": "weekly event-frequency concentration; descriptive, not causal",
            }
        )

    _, structural = read_csv(
        resolve_inside(dataset_root, by_name["structural_metrics"]["path"])
    )
    structural_results = []
    for row in structural:
        if row["layer"] != "all_actions":
            continue
        structural_results.append(
            {
                "chain": row["chain"],
                "event_count": int(row["event_count"]),
                "topology_node_count": int(row["topology_node_count"]),
                "topology_edge_count": int(row["topology_edge_count"]),
                "maximum_k_core": int(row["maximum_k_core"]),
                "weighted_out_degree_hhi": row["weighted_out_degree_hhi"],
                "weighted_in_degree_hhi": row["weighted_in_degree_hhi"],
                "pagerank_hhi": row["pagerank_hhi"],
                "observed_unit": row["observed_unit"],
                "evidence_status": "DERIVED",
                "interpretation_status": row["interpretation_status"],
            }
        )
    structural_results.sort(key=lambda item: item["chain"])

    _, diagnostics = read_csv(
        resolve_inside(dataset_root, by_name["failed_design_estimates"]["path"])
    )
    failed_design_results = []
    for row in diagnostics:
        if int(row["post_horizon"]) != 16:
            continue
        if row["causal_status"] != "diagnostic_not_causal":
            raise ReleaseError("failed-design row is not explicitly non-causal")
        failed_design_results.append(
            {
                "outcome_id": row["outcome_id"],
                "post_horizon": 16,
                "difference_in_changes_arbitrum_minus_gnosis": row[
                    "difference_in_changes_arbitrum_minus_gnosis"
                ],
                "nw_standard_error": row["nw_standard_error"],
                "ci_lower": row["ci_lower"],
                "ci_upper": row["ci_upper"],
                "evidence_status": "FAILED_DESIGN",
                "causal_status": row["causal_status"],
            }
        )
    failed_design_results.sort(key=lambda item: item["outcome_id"])

    _, bounds = read_csv(
        resolve_inside(dataset_root, by_name["actor_bounds_change"]["path"])
    )
    evidence_bounds = [row for row in bounds if row.get("assumption") == "evidence"]
    if len(evidence_bounds) != 1:
        raise ReleaseError("expected exactly one evidence-assumption actor-bound row")
    bound = evidence_bounds[0]
    if bound["economic_actor_conclusion_permitted"].lower() != "false":
        raise ReleaseError("actor evidence unexpectedly permits an economic-actor conclusion")
    actor_result = {
        "metric": bound["metric"],
        "comparison": bound["comparison"],
        "change_lower": bound["change_lower"],
        "change_upper": bound["change_upper"],
        "sign_identified": False,
        "economic_actor_conclusion_permitted": False,
        "evidence_status": "BOUNDED",
    }

    inventory = []
    for name in sorted(by_name):
        item = by_name[name]
        relative = item["path"]
        inventory.append(
            {
                "name": name,
                "path": relative,
                "rows": item["rows"],
                "evidence_status": item["evidence_status"],
                "sha256": checksums[relative],
                "promoted_to_result": item["evidence_status"] in RESULT_EVIDENCE_STATES
                and name
                in {
                    "participation_and_concentration_metrics",
                    "structural_metrics",
                    "failed_design_estimates",
                    "actor_bounds_change",
                },
            }
        )

    return {
        "schema_version": "1.0",
        "release_family": config["release_family"],
        "dataset": {
            "repository": config["dataset"]["repository"],
            "candidate_revision": config["dataset"]["candidate_revision"],
            "locked_revision": config["dataset"]["locked_revision"],
            "package_version": config["dataset"]["package_version"],
            "verified_checksum_file_count": len(verified_paths),
        },
        "inventory": inventory,
        "results": {
            "event_totals": event_totals,
            "weekly_beneficiary_hhi": hhi_results,
            "all_actions_structural_snapshot": structural_results,
            "failed_design_horizon_16": failed_design_results,
            "economic_actor_hhi_change_bound": actor_result,
        },
        "evidence_boundaries": config["evidence_boundaries"],
        "cross_repository_candidates": config["cross_repository_candidates"],
        "final_cross_repository_lock": config["final_cross_repository_lock"],
    }


def compare(reference: Any, observed: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(reference, dict) and isinstance(observed, dict):
        if set(reference) != set(observed):
            errors.append(
                f"{path}: keys differ; expected {sorted(reference)}, got {sorted(observed)}"
            )
            return errors
        for key in sorted(reference):
            errors.extend(compare(reference[key], observed[key], f"{path}.{key}"))
        return errors
    if isinstance(reference, list) and isinstance(observed, list):
        if len(reference) != len(observed):
            return [f"{path}: lengths differ; expected {len(reference)}, got {len(observed)}"]
        for index, (left, right) in enumerate(zip(reference, observed, strict=True)):
            errors.extend(compare(left, right, f"{path}[{index}]"))
        return errors
    if reference != observed:
        errors.append(f"{path}: expected {reference!r}, got {observed!r}")
    return errors


def write_outputs(output_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashes = {"results.json": sha256_file(results_path)}
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )
    return hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the canonical Aave-BNS dataset and reproduce RC8 result snapshots."
    )
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "release" / "reproduction_config.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "release_review",
    )
    parser.add_argument(
        "--checksum-scope",
        choices=("all", "required"),
        default="all",
        help="'all' is the reviewer/release gate; 'required' is for the minimal smoke fixture.",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="Override the committed reference-results path.",
    )
    parser.add_argument(
        "--skip-reference",
        action="store_true",
        help="Generate results without comparison; never used by the release gate.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_json(args.config)
        validate_config(config)
        dataset_root = args.dataset_root.resolve()
        manifest_relative = config["dataset"]["release_manifest"]
        checksum_relative = config["dataset"]["checksum_manifest"]
        manifest = load_json(resolve_inside(dataset_root, manifest_relative))
        by_name, _config_paths = validate_dataset_manifest(dataset_root, manifest, config)
        transformation_inputs = set(config["transformation_inputs"])
        required_paths = transformation_inputs | {manifest_relative}
        checksums = parse_checksums(resolve_inside(dataset_root, checksum_relative))
        verified = verify_checksums(
            dataset_root, checksums, required_paths, args.checksum_scope
        )
        payload = compute_results(dataset_root, by_name, config, verified, checksums)
        hashes = write_outputs(args.output_dir, payload)

        if not args.skip_reference:
            reference_path = args.reference
            if reference_path is None:
                reference_path = resolve_inside(ROOT, config["reference"]["results"])
            reference = load_json(reference_path)
            errors = compare(reference, payload)
            if errors:
                raise ReleaseError(
                    "reference comparison failed:\n  - " + "\n  - ".join(errors[:20])
                )
        print(
            "PASS: canonical package verified; deterministic result snapshot "
            f"sha256={hashes['results.json']}"
        )
        if config["dataset"]["locked_revision"] is None:
            print(
                "NOTICE: candidate dataset commit is immutable, but the final "
                "cross-repository lock remains a human release decision."
            )
        return 0
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
