from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from aave_bns.evm_rpc import canonical_json_sha256, canonicalize_logs
from aave_bns.provenance import sha256_file

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def gzip_payload_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path(value: str) -> bool:
    posix = PurePosixPath(value)
    return (
        bool(value)
        and not posix.is_absolute()
        and not PureWindowsPath(value).is_absolute()
        and ".." not in posix.parts
        and "\\" not in value
    )


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def compare_expected(
    actual: dict[str, Any], expected: dict[str, Any], label: str, failures: list[str]
) -> None:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        require(
            actual_value == expected_value,
            f"{label}.{key}: expected {expected_value!r}, found {actual_value!r}",
            failures,
        )


def verify(root: Path, *, require_local_data: bool) -> tuple[list[str], str]:
    failures: list[str] = []
    lock_path = root / "data/metadata/real_v2_ethereum_release_lock.json"
    output = root / "outputs/real_v2/ethereum"
    lock = read_json(lock_path)
    summary = read_json(output / "summary.json")
    manifest = read_json(output / "manifest.json")
    chunks = read_csv(output / "retrieval_chunks.csv")

    compare_expected(summary, lock["summary"], "summary", failures)
    compare_expected(summary["window"], lock["window"], "summary.window", failures)
    require(
        manifest.get("schema_version") == 2,
        "manifest.schema_version must be 2",
        failures,
    )
    require(
        manifest.get("path_policy") == "repository_relative_posix",
        "manifest.path_policy must be repository_relative_posix",
        failures,
    )
    require(
        manifest.get("raw_log_canonical_sha256") == lock["raw_log_canonical_sha256"],
        "canonical raw-log SHA-256 differs from the release lock",
        failures,
    )
    require(
        manifest.get("processed_event_canonical_csv_sha256")
        == lock["processed_event_canonical_csv_sha256"],
        "processed-event canonical CSV SHA-256 differs from the release lock",
        failures,
    )
    migration = manifest.get("metadata_migration", {})
    revision = str(manifest.get("extraction_source_revision", ""))
    if migration:
        require(
            revision == lock["extraction_source_revision"],
            "published extraction source revision differs from the release lock",
            failures,
        )
        for flag in (
            "processed_gzip_rebuilt_from_locked_raw_chunks",
            "raw_log_canonical_sha256_unchanged",
            "reported_panels_unchanged",
        ):
            require(
                migration.get(flag) is True,
                f"metadata migration flag is false: {flag}",
                failures,
            )
    else:
        require(
            revision == "unavailable" or bool(GIT_REVISION_PATTERN.fullmatch(revision)),
            "replication manifest lacks a valid extraction source revision",
            failures,
        )
    require(
        len(chunks) == lock["summary"]["retrieval_chunk_count"],
        "retrieval_chunks.csv row count differs from the release lock",
        failures,
    )
    require(
        len(manifest.get("raw_chunks", [])) == len(chunks),
        "manifest raw_chunks and retrieval_chunks.csv have different lengths",
        failures,
    )

    if chunks:
        require("path" in chunks[0], "retrieval_chunks.csv lacks path column", failures)
        require(
            "local_path" not in chunks[0],
            "retrieval_chunks.csv still exposes local_path",
            failures,
        )

    manifest_chunks = manifest.get("raw_chunks", [])
    for index, (csv_row, manifest_row) in enumerate(
        zip(chunks, manifest_chunks, strict=False)
    ):
        for row_name, row in (("csv", csv_row), ("manifest", manifest_row)):
            value = str(row.get("path", ""))
            require(
                portable_path(value),
                f"chunk {index} {row_name} path is not portable",
                failures,
            )
        require(
            csv_row.get("path") == manifest_row.get("path"),
            f"chunk {index} path differs between CSV and manifest",
            failures,
        )
        require(
            int(csv_row["from_block"]) == int(manifest_row["from_block"])
            and int(csv_row["to_block"]) == int(manifest_row["to_block"]),
            f"chunk {index} block range differs between CSV and manifest",
            failures,
        )
        if index:
            require(
                int(csv_row["from_block"]) == int(chunks[index - 1]["to_block"]) + 1,
                f"chunk {index} is not contiguous with its predecessor",
                failures,
            )

    if chunks:
        require(
            int(chunks[0]["from_block"]) == lock["window"]["first_block"],
            "first retrieval block differs from the release lock",
            failures,
        )
        require(
            int(chunks[-1]["to_block"]) == lock["window"]["last_block"],
            "last retrieval block differs from the release lock",
            failures,
        )

    for mapping_name in ("artifacts", "configuration", "calendar", "source_code"):
        mapping = manifest.get(mapping_name, {})
        values = mapping.values() if mapping_name in {"artifacts", "source_code"} else [mapping]
        for item in values:
            digest = item if isinstance(item, str) else item.get("sha256", "")
            require(
                isinstance(digest, str) and bool(SHA256_PATTERN.fullmatch(digest)),
                f"manifest {mapping_name} contains an invalid SHA-256",
                failures,
            )

    for mapping_name in ("artifacts", "source_code"):
        for relative in manifest[mapping_name]:
            require(
                portable_path(relative),
                f"manifest {mapping_name} path is not portable: {relative}",
                failures,
            )

    for relative, expected_hash in manifest["artifacts"].items():
        path = root / relative
        if path.is_file():
            require(
                sha256_file(path) == expected_hash,
                f"manifest artifact hash mismatch: {relative}",
                failures,
            )

    for relative, expected_hash in lock["published_artifacts"].items():
        path = root / relative
        require(path.is_file(), f"published artifact is missing: {relative}", failures)
        if path.is_file():
            require(
                sha256_file(path) == expected_hash,
                f"published artifact hash mismatch: {relative}",
                failures,
            )
        if relative != "outputs/real_v2/ethereum/manifest.json":
            require(
                manifest["artifacts"].get(relative) == expected_hash,
                f"manifest does not pin the release hash for: {relative}",
                failures,
            )

    for section in ("configuration", "calendar"):
        item = manifest[section]
        relative = str(item["path"])
        require(portable_path(relative), f"manifest {section} path is not portable", failures)
        path = root / relative
        require(path.is_file(), f"manifest {section} file is missing: {relative}", failures)
        if path.is_file():
            require(
                sha256_file(path) == item["sha256"],
                f"manifest {section} hash mismatch: {relative}",
                failures,
            )

    local_paths = [root / row["path"] for row in manifest_chunks]
    processed_path = root / lock["local_artifacts"]["processed_events"]["path"]
    all_raw_present = bool(local_paths) and all(path.is_file() for path in local_paths)
    processed_present = processed_path.is_file()
    if require_local_data:
        require(all_raw_present, "full audit requires all 165 raw chunk files", failures)
        require(processed_present, "full audit requires the decoded event file", failures)

    tier = "published-audit"
    if all_raw_present and processed_present:
        tier = "full-local-data"
        raw_logs: list[dict[str, Any]] = []
        for row, path in zip(manifest_chunks, local_paths, strict=True):
            require(
                sha256_file(path) == row["compressed_file_sha256"],
                f"raw chunk hash mismatch: {row['path']}",
                failures,
            )
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    logs = [json.loads(line) for line in handle if line.strip()]
            except (EOFError, OSError, json.JSONDecodeError) as error:
                failures.append(f"raw chunk is unreadable: {row['path']}: {error}")
                continue
            require(
                len(logs) == int(row["log_count"]),
                f"raw chunk log count mismatch: {row['path']}",
                failures,
            )
            require(
                canonical_json_sha256(canonicalize_logs(logs))
                == row["canonical_log_sha256"],
                f"raw chunk canonical hash mismatch: {row['path']}",
                failures,
            )
            raw_logs.extend(logs)
        require(
            canonical_json_sha256(canonicalize_logs(raw_logs))
            == lock["raw_log_canonical_sha256"],
            "combined raw-log canonical SHA-256 mismatch",
            failures,
        )
        processed_lock = lock["local_artifacts"]["processed_events"]
        try:
            require(
                gzip_payload_sha256(processed_path)
                == processed_lock["canonical_csv_sha256"],
                "decoded event canonical CSV SHA-256 mismatch",
                failures,
            )
            with gzip.open(processed_path, "rt", encoding="utf-8", newline="") as handle:
                processed_rows = sum(1 for _ in csv.DictReader(handle))
            require(
                processed_rows == lock["summary"]["event_count"],
                "decoded event row count differs from the release lock",
                failures,
            )
        except (EOFError, OSError, csv.Error) as error:
            failures.append(f"decoded event file is unreadable: {error}")

    return failures, tier


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the audited Ethereum real_v2 release lock and local data when present"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--require-local-data",
        action="store_true",
        help="fail unless all raw chunks and the decoded event table are present",
    )
    args = parser.parse_args()
    failures, tier = verify(Path(args.root).resolve(), require_local_data=args.require_local_data)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print(f"PASS: Ethereum real_v2 {tier} verification completed")
    print("events=118806 chunks=165 canonical_raw_sha256=594dbed52b82...f309")
    if tier == "published-audit":
        print("Raw chunks are not required for this tier; rerun after extraction for a full audit.")


if __name__ == "__main__":
    main()
