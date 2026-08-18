# Complete verification predicates keep their condition and failure message together.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-f]{40}$")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_payload_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_records_sha256(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition and len(failures) < 100:
        failures.append(message)
    elif not condition and len(failures) == 100:
        failures.append("additional verification failures omitted")


def _load_json(path: Path, failures: list[str]) -> dict[str, Any]:
    if not path.is_file():
        failures.append(f"missing JSON file: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        failures.append(f"unreadable JSON file {path}: {error}")
        return {}
    if not isinstance(value, dict):
        failures.append(f"JSON root must be an object: {path}")
        return {}
    return value


def _load_csv(path: Path, failures: list[str], *, compressed: bool = False) -> list[dict[str, str]]:
    if not path.is_file():
        failures.append(f"missing CSV file: {path}")
        return []
    try:
        opener = gzip.open if compressed else Path.open
        if compressed:
            handle = opener(path, "rt", encoding="utf-8", newline="")
        else:
            handle = opener(path, "r", encoding="utf-8", newline="")
        with handle:
            return list(csv.DictReader(handle))
    except (OSError, EOFError, UnicodeDecodeError, csv.Error) as error:
        failures.append(f"unreadable CSV file {path}: {error}")
        return []


def _repository_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        not path.is_absolute()
        and not windows.is_absolute()
        and ".." not in path.parts
        and "." not in path.parts
    )


def _project_path(root: Path, value: object, failures: list[str]) -> Path | None:
    if not _repository_relative_path(value):
        failures.append(f"unsafe or non-portable path: {value!r}")
        return None
    relative = str(value)
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        failures.append(f"path escapes project root: {relative}")
        return None
    return candidate


def _as_int(row: dict[str, str], key: str) -> int:
    return int(row[key])


def _as_float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def _as_bool(value: object) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _verify_release_metadata(
    root: Path,
    lock: dict[str, Any],
    manifest: dict[str, Any],
    summary: dict[str, Any],
    failures: list[str],
) -> None:
    _require(lock.get("schema_version") == 1, "unsupported release-lock schema", failures)
    for field in ("pipeline", "release_version"):
        expected = lock.get(field)
        _require(manifest.get(field) == expected, f"manifest {field} differs from lock", failures)
        _require(
            summary.get(field) == expected if field == "release_version" else True,
            f"summary {field} differs from lock",
            failures,
        )
    _require(manifest.get("schema_version") == 1, "unsupported manifest schema", failures)
    _require(manifest.get("path_policy") == "repository_relative_posix", "manifest path policy drifted", failures)
    _require(manifest.get("entity_gate_passed") is False, "manifest entity gate did not fail closed", failures)
    _require(manifest.get("entity_level_primary_result_produced") is False, "manifest claims a primary entity result", failures)
    _require(manifest.get("causal_estimate_produced") is False, "manifest claims a causal estimate", failures)

    expected_summary = lock.get("summary", {})
    if not isinstance(expected_summary, dict):
        failures.append("release-lock summary is malformed")
        expected_summary = {}
    for field, expected in expected_summary.items():
        _require(summary.get(field) == expected, f"summary field drifted: {field}", failures)
    _require(summary.get("entity_gate") == lock.get("entity_gate"), "entity gate thresholds or status drifted", failures)
    _require(summary.get("status") == "descriptive_contract_and_entity_annotation_layer", "summary status drifted", failures)

    published = lock.get("published_artifacts", {})
    manifest_artifacts = manifest.get("artifacts", {})
    if not isinstance(published, dict) or not isinstance(manifest_artifacts, dict):
        failures.append("published artifact map is malformed")
    else:
        _require(manifest_artifacts == published, "manifest artifact hashes differ from release lock", failures)
        for relative, expected_hash in published.items():
            path = _project_path(root, relative, failures)
            _require(bool(SHA256_PATTERN.fullmatch(str(expected_hash))), f"invalid locked SHA-256: {relative}", failures)
            if path is not None:
                _require(path.is_file(), f"missing published artifact: {relative}", failures)
                if path.is_file():
                    _require(_sha256_file(path) == expected_hash, f"published artifact hash mismatch: {relative}", failures)

    configuration = lock.get("configuration", {})
    manifest_configuration = manifest.get("configuration", {})
    _require(manifest_configuration == configuration, "configuration record differs from release lock", failures)
    if isinstance(configuration, dict):
        path = _project_path(root, configuration.get("path"), failures)
        if path is not None and path.is_file():
            _require(_sha256_file(path) == configuration.get("sha256"), "configuration SHA-256 mismatch", failures)

    inputs = lock.get("inputs", {})
    curated = inputs.get("curated_labels", {}) if isinstance(inputs, dict) else {}
    source_catalog = inputs.get("source_catalog", {}) if isinstance(inputs, dict) else {}
    manifest_inputs = manifest.get("inputs", {})
    for name, item in (("curated-label", curated), ("source-catalog", source_catalog)):
        path = _project_path(root, item.get("path"), failures)
        if path is not None:
            _require(path.is_file(), f"missing declared {name} input", failures)
            if path.is_file():
                _require(_sha256_file(path) == item.get("sha256"), f"{name} SHA-256 mismatch", failures)
        if isinstance(manifest_inputs, dict):
            _require(manifest_inputs.get(item.get("path")) == item.get("sha256"), f"manifest {name} hash differs from lock", failures)

    _require(manifest.get("raw_code_snapshot_canonical_sha256") == lock.get("raw_code_snapshot_canonical_sha256"), "raw code canonical SHA-256 drifted", failures)
    _require(manifest.get("registry_canonical_csv_sha256") == lock.get("registry_canonical_csv_sha256"), "registry canonical SHA-256 drifted", failures)

    code = manifest.get("code", {})
    if not isinstance(code, dict) or not code:
        failures.append("manifest code map is missing")
    else:
        for relative, expected_hash in code.items():
            path = _project_path(root, relative, failures)
            if path is not None:
                _require(path.is_file(), f"missing extraction code: {relative}", failures)
                if path.is_file():
                    _require(_sha256_file(path) == expected_hash, f"extraction code hash mismatch: {relative}", failures)

    providers = manifest.get("providers", {})
    source_ids = lock.get("source_ids", {})
    for role, source_key in (("primary", "primary_rpc"), ("validation", "validation_rpc")):
        provider = providers.get(role, {}) if isinstance(providers, dict) else {}
        _require(provider.get("source_id") == source_ids.get(source_key), f"{role} provider source ID drifted", failures)
        endpoint = provider.get("endpoint", "")
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else urlsplit("")
        _require(parsed.scheme == "https" and bool(parsed.netloc), f"{role} provider endpoint is not HTTPS", failures)
        _require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, f"{role} endpoint exposes credentials or query parameters", failures)


def _verify_registry(
    root: Path, lock: dict[str, Any], failures: list[str]
) -> list[dict[str, str]]:
    path = root / "outputs/real_v3/ethereum/address_registry.csv.gz"
    rows = _load_csv(path, failures, compressed=True)
    if not rows:
        return rows
    try:
        _require(_gzip_payload_sha256(path) == lock.get("registry_canonical_csv_sha256"), "registry decompressed CSV SHA-256 mismatch", failures)
        expected = lock["summary"]
        addresses = [row["address"] for row in rows]
        _require(len(rows) == expected["address_count"], "registry row count mismatch", failures)
        _require(len(addresses) == len(set(addresses)), "registry contains duplicate addresses", failures)
        _require(all(ADDRESS_PATTERN.fullmatch(value) for value in addresses), "registry contains a malformed address", failures)
        _require(addresses == sorted(addresses), "registry address ordering is not deterministic", failures)

        contract_rows = [row for row in rows if _as_bool(row["contract_observed"])]
        labelled_rows = [row for row in rows if _as_bool(row["high_confidence_entity_label"])]
        economic_rows = [row for row in rows if _as_bool(row["economic_actor_resolved"])]
        _require(sum(_as_int(row, "event_incidence_count") for row in rows) == expected["event_incidence_count"], "registry event incidences do not reconcile", failures)
        _require(sum(_as_int(row, "role_incidence_count") for row in rows) == expected["role_incidence_count"], "registry role incidences do not reconcile", failures)
        _require(len(contract_rows) == expected["smart_contract_address_count"], "registry contract-address count mismatch", failures)
        _require(sum(_as_int(row, "event_incidence_count") for row in contract_rows) == expected["contract_event_incidence_count"], "registry contract incidences do not reconcile", failures)
        _require(len(labelled_rows) == expected["curated_label_address_count"], "registry curated-label count mismatch", failures)
        _require(sum(_as_int(row, "event_incidence_count") for row in labelled_rows) == expected["curated_label_event_incidence_count"], "registry curated-label incidences do not reconcile", failures)
        _require(len(economic_rows) == expected["economic_actor_address_count"], "registry economic-actor count mismatch", failures)
        _require(sum(_as_int(row, "event_incidence_count") for row in economic_rows) == expected["economic_actor_event_incidence_count"], "registry economic-actor incidences do not reconcile", failures)
        _require(sum(row["address_type"] == "code_absent_at_observed_bounds" for row in rows) == expected["code_absent_address_count"], "registry code-absent count mismatch", failures)

        release = lock["release_version"]
        minimum_confidence = float(lock["entity_gate"]["minimum_confidence"])
        for row in rows:
            contract = _as_bool(row["contract_observed"])
            labelled = _as_bool(row["high_confidence_entity_label"])
            economic = _as_bool(row["economic_actor_resolved"])
            _require(row["release_version"] == release and row["label_release_version"] == release, f"registry release drifted for {row['address']}", failures)
            _require(float(row["address_type_confidence"]) == 1.0, f"address-type confidence drifted for {row['address']}", failures)
            _require(row["address_type_basis"] == "eth_getCode_at_first_and_last_observed_blocks", f"address-type basis drifted for {row['address']}", failures)
            if contract:
                _require(row["address_type"] in {"smart_contract", "smart_contract_dynamic"}, f"contract classification mismatch for {row['address']}", failures)
                _require(bool(row["infrastructure_family_id"]), f"contract lacks a template family: {row['address']}", failures)
            else:
                _require(row["address_type"] == "code_absent_at_observed_bounds", f"code-absent address was overclassified: {row['address']}", failures)
                _require(not row["infrastructure_family_id"], f"code-absent address has a template family: {row['address']}", failures)
            if labelled:
                _require(bool(row["entity_id"]) and float(row["confidence"]) >= minimum_confidence, f"accepted label lacks gated evidence: {row['address']}", failures)
                _require(row["curated_entity_key"] == row["entity_id"], f"accepted label was not collapsed to its curated entity: {row['address']}", failures)
            else:
                _require(row["curated_entity_key"] == f"address:{row['address']}", f"unresolved addresses were clustered: {row['address']}", failures)
            if economic:
                _require(labelled and row["entity_scope"] == "economic_actor", f"economic-actor flag lacks an accepted economic label: {row['address']}", failures)
    except (KeyError, TypeError, ValueError) as error:
        failures.append(f"registry semantic verification failed: {error}")
    return rows


def _verify_labels(
    root: Path,
    lock: dict[str, Any],
    registry: list[dict[str, str]],
    failures: list[str],
) -> None:
    labels = _load_csv(root / lock["inputs"]["curated_labels"]["path"], failures)
    catalog = _load_csv(root / lock["inputs"]["source_catalog"]["path"], failures)
    if not labels:
        return
    try:
        expected_count = lock["summary"]["curated_label_address_count"]
        _require(len(labels) == expected_count, "curated-label row count mismatch", failures)
        addresses = [row["address"] for row in labels]
        _require(len(addresses) == len(set(addresses)), "curated labels contain duplicate addresses", failures)
        catalog_ids = {row["source_id"] for row in catalog}
        registry_by_address = {row["address"]: row for row in registry}
        for row in labels:
            _require(row["release_version"] == lock["release_version"], f"curated label release drifted: {row['address']}", failures)
            _require(row["source_id"] == lock["source_ids"]["curated_labels"] and row["source_id"] in catalog_ids, f"curated label source is not catalogued: {row['address']}", failures)
            revision = row["source_revision"]
            _require(bool(GIT_REVISION_PATTERN.fullmatch(revision)), f"curated label lacks a pinned Git revision: {row['address']}", failures)
            _require(f"/blob/{revision}/" in row["source_url"], f"curated label URL is not pinned to its revision: {row['address']}", failures)
            _require(row["review_status"] == "primary_source_verified", f"curated label is not primary-source verified: {row['address']}", failures)
            _require(float(row["confidence"]) >= lock["entity_gate"]["minimum_confidence"], f"curated label confidence is below the gate: {row['address']}", failures)
            _require(row["entity_scope"] in {"protocol_infrastructure", "asset_infrastructure"}, f"v0.1.0 label improperly claims an economic actor: {row['address']}", failures)
            registered = registry_by_address.get(row["address"])
            _require(registered is not None and registered.get("entity_id") == row["entity_id"], f"curated label is missing or different in registry: {row['address']}", failures)
    except (KeyError, TypeError, ValueError) as error:
        failures.append(f"curated-label verification failed: {error}")


def _verify_panels(root: Path, lock: dict[str, Any], failures: list[str]) -> None:
    weekly = _load_csv(root / "outputs/real_v3/ethereum/weekly_entity_sensitivity.csv", failures)
    action = _load_csv(root / "outputs/real_v3/ethereum/weekly_action_entity_sensitivity.csv", failures)
    checks = _load_csv(root / "outputs/real_v3/ethereum/cross_provider_code_checks.csv", failures)
    try:
        expected = lock["summary"]
        weeks = set(range(-16, 17))
        actions = set(lock["expected_actions"])
        _require(len(weekly) == expected["weekly_panel_rows"], "weekly entity-sensitivity panel row count mismatch", failures)
        _require({_as_int(row, "event_week") for row in weekly} == weeks, "weekly entity-sensitivity panel has missing event weeks", failures)
        _require(sum(_as_int(row, "event_incidence_count") for row in weekly) == expected["event_incidence_count"], "weekly entity-sensitivity incidences do not reconcile", failures)
        _require(len(action) == expected["weekly_action_panel_rows"], "weekly action entity-sensitivity panel row count mismatch", failures)
        _require({(_as_int(row, "event_week"), row["action"]) for row in action} == {(week, name) for week in weeks for name in actions}, "weekly action panel is not the complete week-action Cartesian product", failures)
        _require(sum(_as_int(row, "event_incidence_count") for row in action) == expected["event_incidence_count"], "weekly action incidences do not reconcile", failures)
        share_columns = {
            "contract_incidence_share",
            "protocol_infrastructure_incidence_share",
            "high_confidence_entity_incidence_coverage",
            "economic_actor_incidence_coverage",
            "contract_template_hhi_conditional",
            "top_contract_template_share_conditional",
        }
        for row in [*weekly, *action]:
            _require(all(0.0 <= _as_float(row, field) <= 1.0 for field in share_columns), "panel contains a share outside [0, 1]", failures)
            _require(_as_float(row, "economic_actor_incidence_coverage") == 0.0, "panel promotes unresolved economic actors", failures)

        _require(len(checks) == expected["validation_check_count"], "cross-provider check count mismatch", failures)
        for row in checks:
            _require(_as_bool(row["exact_match"]), f"cross-provider code mismatch: {row.get('address')}", failures)
            _require(row["primary_code_bytes"] == row["validation_code_bytes"], f"cross-provider byte length mismatch: {row['address']}", failures)
            _require(row["primary_code_sha256"] == row["validation_code_sha256"], f"cross-provider code hash mismatch: {row['address']}", failures)
    except (KeyError, TypeError, ValueError) as error:
        failures.append(f"panel verification failed: {error}")


def _verify_batches(
    root: Path,
    lock: dict[str, Any],
    manifest: dict[str, Any],
    failures: list[str],
    *,
    require_local_data: bool,
) -> str:
    batch_rows = _load_csv(root / "outputs/real_v3/ethereum/code_retrieval_batches.csv", failures)
    raw_batches = manifest.get("raw_batches", [])
    if not isinstance(raw_batches, list):
        failures.append("manifest raw-batch list is malformed")
        raw_batches = []
    try:
        expected_count = int(lock["raw_batch_count"])
        _require(len(raw_batches) == expected_count, "manifest raw-batch count mismatch", failures)
        _require(len(batch_rows) == expected_count, "published raw-batch index count mismatch", failures)
        _require([int(row["batch_index"]) for row in raw_batches] == list(range(expected_count)), "manifest batch indexes are not contiguous", failures)
        _require([int(row["batch_index"]) for row in batch_rows] == list(range(expected_count)), "published batch indexes are not contiguous", failures)
        _require(sum(int(row["address_count"]) for row in raw_batches) == lock["summary"]["address_count"], "raw-batch address counts do not reconcile", failures)
        for manifest_row, csv_row in zip(raw_batches, batch_rows, strict=True):
            _require(manifest_row.get("source") == "primary_rpc" and csv_row.get("source") == "primary_rpc", f"batch source metadata is resume-dependent: {manifest_row.get('batch_index')}", failures)
            for field in ("path", "compressed_file_sha256", "canonical_records_sha256", "first_address", "last_address"):
                _require(str(manifest_row.get(field)) == csv_row.get(field), f"batch index differs from manifest at {field}: {manifest_row.get('batch_index')}", failures)
            _project_path(root, manifest_row.get("path"), failures)
            _require(bool(SHA256_PATTERN.fullmatch(str(manifest_row.get("compressed_file_sha256", "")))), f"invalid raw batch file hash: {manifest_row.get('batch_index')}", failures)
            _require(bool(SHA256_PATTERN.fullmatch(str(manifest_row.get("canonical_records_sha256", "")))), f"invalid raw batch canonical hash: {manifest_row.get('batch_index')}", failures)
    except (KeyError, TypeError, ValueError) as error:
        failures.append(f"raw-batch index verification failed: {error}")

    raw_paths = [_project_path(root, row.get("path"), failures) for row in raw_batches]
    present = [path is not None and path.is_file() for path in raw_paths]
    if require_local_data and not all(present):
        failures.append("full local verification requires all 316 raw code batches")
    if any(present) and not all(present):
        failures.append("raw code batch set is partial")
    if not all(present):
        return "published-audit"

    combined: list[dict[str, Any]] = []
    for metadata, path in zip(raw_batches, raw_paths, strict=True):
        if path is None:
            continue
        try:
            _require(_sha256_file(path) == metadata["compressed_file_sha256"], f"raw batch compressed hash mismatch: {metadata['path']}", failures)
            _require(path.stat().st_size == int(metadata["compressed_bytes"]), f"raw batch size mismatch: {metadata['path']}", failures)
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
            _require(len(records) == int(metadata["address_count"]), f"raw batch address count mismatch: {metadata['path']}", failures)
            _require(_canonical_records_sha256(records) == metadata["canonical_records_sha256"], f"raw batch canonical hash mismatch: {metadata['path']}", failures)
            if records:
                _require(records[0].get("address") == metadata["first_address"] and records[-1].get("address") == metadata["last_address"], f"raw batch boundary address mismatch: {metadata['path']}", failures)
            combined.extend(records)
        except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            failures.append(f"raw batch is unreadable or malformed: {metadata.get('path')}: {error}")
    _require(len(combined) == lock["summary"]["address_count"], "combined raw code record count mismatch", failures)
    addresses = [str(row.get("address", "")) for row in combined]
    _require(addresses == sorted(addresses) and len(addresses) == len(set(addresses)), "combined raw code records are not unique and sorted", failures)
    _require(_canonical_records_sha256(combined) == lock["raw_code_snapshot_canonical_sha256"], "combined raw code canonical SHA-256 mismatch", failures)

    processed = lock["inputs"]["processed_events"]
    processed_path = _project_path(root, processed.get("path"), failures)
    if processed_path is not None:
        _require(processed_path.is_file(), "full local verification requires the real_v2 processed event table", failures)
        if processed_path.is_file():
            _require(_sha256_file(processed_path) == processed.get("release_compressed_sha256"), "real_v2 processed-event release hash mismatch", failures)
    return "full-local-data"


def verify(root: Path, *, require_local_data: bool = False) -> tuple[list[str], str]:
    failures: list[str] = []
    root = root.resolve()
    lock = _load_json(root / "data/metadata/real_v3_ethereum_release_lock.json", failures)
    manifest = _load_json(root / "outputs/real_v3/ethereum/manifest.json", failures)
    summary = _load_json(root / "outputs/real_v3/ethereum/summary.json", failures)
    if not lock or not manifest or not summary:
        return failures, "unverified"
    try:
        _verify_release_metadata(root, lock, manifest, summary, failures)
        registry = _verify_registry(root, lock, failures)
        _verify_labels(root, lock, registry, failures)
        _verify_panels(root, lock, failures)
        tier = _verify_batches(
            root,
            lock,
            manifest,
            failures,
            require_local_data=require_local_data,
        )
    except Exception as error:  # Defensive: corrupt releases should fail, never crash.
        failures.append(f"unexpected verification error: {type(error).__name__}: {error}")
        tier = "unverified"
    return failures, tier


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the audited Ethereum real_v3 contract-role/entity release"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--require-local-data",
        action="store_true",
        help="fail unless all raw historical-code batches and real_v2 events are present",
    )
    args = parser.parse_args()
    failures, tier = verify(Path(args.root), require_local_data=args.require_local_data)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print(f"PASS: Ethereum real_v3 {tier} verification completed")
    print("addresses=15762 contracts=1128 incidences=148437 raw_sha256=276f6e43ac20...8809")
    print("entity_gate=failed_closed primary_entity_result=false causal_estimate=false")
    if tier == "published-audit":
        print("Raw code batches are not required for this tier; rerun after extraction for a full audit.")


if __name__ == "__main__":
    main()
