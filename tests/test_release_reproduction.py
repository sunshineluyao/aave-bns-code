import hashlib
import json
import shutil
from pathlib import Path

from scripts.reproduce_release import main

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "release_minimal"
FIXTURE_CONFIG = FIXTURE / "reproduction_config.json"
FIXTURE_REFERENCE = FIXTURE / "expected_results.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_fixture(dataset_root: Path, output_dir: Path) -> int:
    return main(
        [
            "--dataset-root",
            str(dataset_root),
            "--config",
            str(dataset_root / "reproduction_config.json"),
            "--reference",
            str(FIXTURE_REFERENCE),
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
    rewritten = []
    for line in lines:
        _, path = line.split(maxsplit=1)
        rewritten.append(f"{digest if path == relative else line.split()[0]}  {path}")
    manifest.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def test_smoke_fixture_matches_reference(tmp_path: Path):
    output_dir = tmp_path / "output"
    assert run_fixture(FIXTURE, output_dir) == 0
    assert json.loads((output_dir / "results.json").read_text(encoding="utf-8")) == json.loads(
        FIXTURE_REFERENCE.read_text(encoding="utf-8")
    )
    assert sha256(output_dir / "results.json") == (
        output_dir / "SHA256SUMS.txt"
    ).read_text(encoding="utf-8").split()[0]


def test_mutated_fixture_fails_checksum(tmp_path: Path):
    dataset_root = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dataset_root)
    target = dataset_root / "data" / "processed" / "structural_metrics" / "data.csv"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert run_fixture(dataset_root, tmp_path / "output") == 1


def test_causal_promotion_fails_even_after_rechecksum(tmp_path: Path):
    dataset_root = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dataset_root)
    relative = "data/processed/failed_design_estimates/data.csv"
    target = dataset_root / relative
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "diagnostic_not_causal", "causal_estimate", 1
        ),
        encoding="utf-8",
    )
    rewrite_checksum(dataset_root, relative)
    assert run_fixture(dataset_root, tmp_path / "output") == 1


def test_committed_canonical_reference_hash():
    expected = (
        ROOT / "release" / "reference_results.sha256"
    ).read_text(encoding="utf-8").split()[0]
    assert sha256(ROOT / "release" / "reference_results.json") == expected


def test_final_lock_is_intentionally_unset():
    config = json.loads(
        (ROOT / "release" / "reproduction_config.json").read_text(encoding="utf-8")
    )
    assert config["dataset"]["locked_revision"] is None
    assert config["final_cross_repository_lock"] is None


def test_hf_configs_are_separate_from_audit_only_gaps():
    config = json.loads(
        (ROOT / "release" / "reproduction_config.json").read_text(encoding="utf-8")
    )
    assert len(config["required_configs"]) == 14
    assert config["metadata_only_evidence_gaps"] == {
        "infrastructure_evidence_status": "BLOCKED",
        "future_route_schema": "BLOCKED",
    }
    assert not (
        set(config["required_configs"]) & set(config["metadata_only_evidence_gaps"])
    )
