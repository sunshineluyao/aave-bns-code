#!/usr/bin/env python3
"""Standard-library smoke gate for the public release reproduction interface."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from reproduce_release import main as reproduce

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "release_minimal"
REFERENCE = FIXTURE / "expected_results.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(dataset_root: Path, output_dir: Path) -> int:
    return reproduce(
        [
            "--dataset-root",
            str(dataset_root),
            "--config",
            str(dataset_root / "reproduction_config.json"),
            "--reference",
            str(REFERENCE),
            "--checksum-scope",
            "all",
            "--output-dir",
            str(output_dir),
        ]
    )


def rewrite_checksum(dataset_root: Path, relative: str) -> None:
    manifest = dataset_root / "metadata" / "checksums.sha256"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    digest = sha256(dataset_root / relative)
    output = []
    for line in lines:
        old_digest, path = line.split(maxsplit=1)
        output.append(f"{digest if path == relative else old_digest}  {path}")
    manifest.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aave-bns-release-smoke-") as temporary:
        temp = Path(temporary)

        success_root = temp / "success"
        shutil.copytree(FIXTURE, success_root)
        success_output = temp / "success-output"
        if invoke(success_root, success_output) != 0:
            raise SystemExit("smoke success case failed")
        observed = json.loads(
            (success_output / "results.json").read_text(encoding="utf-8")
        )
        expected = json.loads(REFERENCE.read_text(encoding="utf-8"))
        if observed != expected:
            raise SystemExit("smoke result differs from committed reference")
        if sha256(success_output / "results.json") != (
            success_output / "SHA256SUMS.txt"
        ).read_text(encoding="utf-8").split()[0]:
            raise SystemExit("smoke output checksum is inconsistent")

        checksum_root = temp / "checksum-failure"
        shutil.copytree(FIXTURE, checksum_root)
        checksum_target = checksum_root / "data" / "processed" / "structural_metrics" / "data.csv"
        checksum_target.write_text(
            checksum_target.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        if invoke(checksum_root, temp / "checksum-output") == 0:
            raise SystemExit("mutated fixture unexpectedly passed checksum validation")

        causal_root = temp / "causal-failure"
        shutil.copytree(FIXTURE, causal_root)
        relative = "data/processed/failed_design_estimates/data.csv"
        causal_target = causal_root / relative
        causal_target.write_text(
            causal_target.read_text(encoding="utf-8").replace(
                "diagnostic_not_causal", "causal_estimate", 1
            ),
            encoding="utf-8",
        )
        rewrite_checksum(causal_root, relative)
        if invoke(causal_root, temp / "causal-output") == 0:
            raise SystemExit("causal promotion unexpectedly passed logical validation")

    print(
        "PASS: release smoke validated reference agreement, checksum rejection, "
        "and failed-design causal boundary."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
