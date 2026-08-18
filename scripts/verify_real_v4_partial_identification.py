from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pandas as pd
from pandas.testing import assert_frame_equal

from aave_bns.provenance import sha256_file
from aave_bns.real_v4_partial_identification import (
    attach_registry,
    build_beneficiary_event_panel,
    build_bound_panels,
    build_change_bounds,
    load_actor_constraints,
    load_address_registry,
    load_real_v4_config,
    validate_event_timing,
)


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition and len(failures) < 100:
        failures.append(message)


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


def _repository_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and ".." not in posix.parts
        and "." not in posix.parts
    )


def _project_path(root: Path, value: object, failures: list[str]) -> Path | None:
    if not _repository_relative(value):
        failures.append(f"unsafe or non-portable path: {value!r}")
        return None
    path = (root / str(value)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        failures.append(f"path escapes project root: {value}")
        return None
    return path


def _gzip_payload_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_frame(
    observed_path: Path,
    expected: pd.DataFrame,
    label: str,
    failures: list[str],
    *,
    compressed: bool = False,
) -> None:
    try:
        observed = pd.read_csv(observed_path, compression="gzip" if compressed else None)
        assert_frame_equal(
            observed,
            expected,
            check_dtype=False,
            check_exact=False,
            rtol=1e-13,
            atol=1e-15,
        )
    except (OSError, EOFError, ValueError, AssertionError) as error:
        failures.append(f"{label} does not recompute: {error}")


def verify(root: Path, *, require_local_data: bool) -> list[str]:
    failures: list[str] = []
    lock_path = root / "data/metadata/real_v4_ethereum_release_lock.json"
    lock = _load_json(lock_path, failures)
    output = root / "outputs/real_v4/ethereum"
    summary = _load_json(output / "summary.json", failures)
    manifest = _load_json(output / "manifest.json", failures)
    config_record = lock.get("configuration", {})
    config_path = _project_path(root, config_record.get("path"), failures)
    if config_path is None or not config_path.is_file():
        failures.append("missing real_v4 configuration")
        return failures
    _require(
        sha256_file(config_path) == config_record.get("sha256"),
        "configuration SHA-256 mismatch",
        failures,
    )
    config = load_real_v4_config(config_path)
    _require(lock.get("schema_version") == 1, "unsupported release-lock schema", failures)
    _require(manifest.get("schema_version") == 1, "unsupported manifest schema", failures)
    _require(
        lock.get("pipeline")
        == manifest.get("pipeline")
        == "real_v4_ethereum_partial_identification",
        "pipeline identifier drifted",
        failures,
    )
    _require(
        lock.get("release_version")
        == manifest.get("release_version")
        == summary.get("release_version")
        == config.get("release_version"),
        "release version drifted",
        failures,
    )
    _require(
        manifest.get("generation_policy") == "deterministic_no_wall_clock",
        "manifest generation policy drifted",
        failures,
    )
    _require(
        manifest.get("path_policy") == "repository_relative_posix",
        "manifest path policy drifted",
        failures,
    )
    for field, expected in lock.get("summary", {}).items():
        _require(summary.get(field) == expected, f"summary field drifted: {field}", failures)
    for field, expected in (
        ("identified_set_produced", True),
        ("economic_actor_direction_identified", False),
        ("entity_level_primary_result_produced", False),
        ("causal_estimate_produced", False),
    ):
        _require(summary.get(field) is expected, f"summary guardrail drifted: {field}", failures)
        _require(manifest.get(field) is expected, f"manifest guardrail drifted: {field}", failures)

    published = lock.get("published_artifacts", {})
    for relative, expected_hash in published.items():
        path = _project_path(root, relative, failures)
        if path is None:
            continue
        _require(path.is_file(), f"missing published artifact: {relative}", failures)
        if path.is_file():
            _require(
                sha256_file(path) == expected_hash,
                f"published artifact hash mismatch: {relative}",
                failures,
            )
    expected_manifest_artifacts = {
        key: value
        for key, value in published.items()
        if key != "outputs/real_v4/ethereum/manifest.json"
    }
    _require(
        manifest.get("artifacts") == expected_manifest_artifacts,
        "manifest artifact map differs from release lock",
        failures,
    )

    declared_inputs = lock.get("inputs", {})
    manifest_inputs = manifest.get("inputs", {})
    expected_manifest_inputs = {
        item["path"]: item.get("sha256") or item.get("release_compressed_sha256")
        for item in declared_inputs.values()
    }
    _require(
        manifest_inputs == expected_manifest_inputs,
        "manifest input map differs from release lock",
        failures,
    )
    for name, item in declared_inputs.items():
        path = _project_path(root, item.get("path"), failures)
        if path is None:
            continue
        required = name != "processed_events" or require_local_data
        if required:
            _require(path.is_file(), f"missing declared input: {name}", failures)
        if path.is_file():
            expected_hash = item.get("sha256") or item.get("release_compressed_sha256")
            _require(
                sha256_file(path) == expected_hash,
                f"declared input hash mismatch: {name}",
                failures,
            )

    beneficiary_path = output / "beneficiary_event_panel.csv.gz"
    _require(beneficiary_path.is_file(), "missing beneficiary event panel", failures)
    if beneficiary_path.is_file():
        _require(
            _gzip_payload_sha256(beneficiary_path)
            == lock.get("beneficiary_event_panel_canonical_sha256"),
            "beneficiary panel canonical SHA-256 mismatch",
            failures,
        )
    try:
        beneficiary = pd.read_csv(beneficiary_path)
        _require(len(beneficiary) == 118_806, "beneficiary panel row count drifted", failures)
        _require(
            beneficiary["event_ordinal"].tolist() == list(range(1, len(beneficiary) + 1)),
            "beneficiary event ordinals are not complete",
            failures,
        )
        _require(
            set(beneficiary["action"]) == {"borrow", "liquidation", "repay", "supply", "withdraw"},
            "beneficiary action set drifted",
            failures,
        )
        _require(
            set(beneficiary["event_week"]) == set(range(-16, 17)),
            "beneficiary event-week coverage drifted",
            failures,
        )
    except (OSError, EOFError, ValueError, KeyError) as error:
        failures.append(f"beneficiary panel is unreadable: {error}")
        return failures

    inputs = config["input"]
    registry = load_address_registry(root / inputs["address_registry"])
    gate = config["constraint_gate"]
    all_constraints, accepted = load_actor_constraints(
        root / inputs["actor_constraints"],
        release_version=str(config["release_version"]),
        chain_id=int(config["chain"]["chain_id"]),
        minimum_confidence=float(gate["minimum_confidence"]),
        allowed_relation=str(gate["allowed_relation"]),
        required_entity_scope=str(gate["required_entity_scope"]),
        required_review_status=str(gate["required_review_status"]),
        registry_addresses=set(registry["address"]),
    )
    weekly_action, weekly, periods = build_bound_panels(
        beneficiary,
        registry,
        accepted,
        list(config["measurement"]["periods"]),
    )
    changes = build_change_bounds(periods)
    _compare_frame(
        output / "weekly_action_beneficiary_bounds.csv",
        weekly_action,
        "weekly action bounds",
        failures,
    )
    _compare_frame(
        output / "weekly_beneficiary_bounds.csv",
        weekly,
        "weekly bounds",
        failures,
    )
    _compare_frame(
        output / "period_beneficiary_bounds.csv",
        periods,
        "period bounds",
        failures,
    )
    _compare_frame(
        output / "period_change_bounds.csv",
        changes,
        "period-change bounds",
        failures,
    )
    _compare_frame(
        output / "constraint_audit.csv",
        all_constraints,
        "constraint audit",
        failures,
    )
    merged = attach_registry(beneficiary, registry)
    _require(
        int(merged["contract_observed"].sum()) == 27_791,
        "contract-beneficiary count drifted",
        failures,
    )
    _require(
        int(merged["curated_infrastructure"].sum()) == 8_827,
        "curated-infrastructure beneficiary count drifted",
        failures,
    )
    _require(len(accepted) == 0, "v0.1.0 unexpectedly accepts actor must-links", failures)
    stable_change = changes.set_index("assumption").loc["stable_address"]
    _require(
        float(stable_change["change_lower"]) < 0 < float(stable_change["change_upper"]),
        "economic-actor HHI direction is no longer unidentified",
        failures,
    )

    validate_event_timing(
        root / inputs["event_source_audit"],
        root / inputs["event_week_calendar"],
        event_id=str(config["event"]["event_id"]),
        activation_block=int(config["event"]["activation_block"]),
        activation_utc=str(config["event"]["activation_utc"]),
    )
    _require(
        config["event"]["activation_utc"] == "2023-07-15T14:02:59Z",
        "on-chain activation timestamp drifted",
        failures,
    )
    _require(
        config["event"]["public_changelog_date"] == "2023-07-16",
        "public changelog date drifted",
        failures,
    )

    if require_local_data:
        processed = root / inputs["processed_events"]
        events = pd.read_csv(processed, dtype=str)
        rebuilt = build_beneficiary_event_panel(
            events,
            chain_id=int(config["chain"]["chain_id"]),
            minimum_event_week=int(config["measurement"]["minimum_event_week"]),
            maximum_event_week=int(config["measurement"]["maximum_event_week"]),
        )
        try:
            assert_frame_equal(rebuilt, beneficiary, check_dtype=False, check_exact=True)
        except AssertionError as error:
            failures.append(f"local real_v2 input does not rebuild beneficiary panel: {error}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the locked real_v4 partial-identification release"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--require-local-data", action="store_true")
    args = parser.parse_args()
    failures = verify(Path(args.root).resolve(), require_local_data=args.require_local_data)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("real_v4 verification passed")
    print("events=118806 beneficiaries=15351 accepted_actor_must_links=0")
    print("address_proxy_hhi_direction=decrease actor_hhi_direction=not_identified")
    print("primary_entity_result=false causal_estimate=false")


if __name__ == "__main__":
    main()
