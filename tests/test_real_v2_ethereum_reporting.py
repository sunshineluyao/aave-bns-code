import csv
import json
from pathlib import Path, PurePosixPath

OUTPUT = Path("outputs/real_v2/ethereum")


def read_csv(name: str) -> list[dict[str, str]]:
    with (OUTPUT / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_audited_summary_reconciles_and_does_not_claim_causality():
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    checks = read_csv("cross_provider_checks.csv")
    assert summary["event_count"] == 118806
    assert sum(summary["action_counts"].values()) == summary["event_count"]
    assert manifest["causal_estimate_produced"] is False
    assert len(checks) == 4
    assert all(row["exact_match"] == "True" for row in checks)


def test_audited_chunk_paths_are_repository_relative():
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    chunks = read_csv("retrieval_chunks.csv")
    assert manifest["schema_version"] == 2
    assert manifest["path_policy"] == "repository_relative_posix"
    assert len(chunks) == len(manifest["raw_chunks"]) == 165
    assert "local_path" not in chunks[0]
    for row in [*chunks, *manifest["raw_chunks"]]:
        path = PurePosixPath(row["path"])
        assert not path.is_absolute()
        assert ".." not in path.parts


def test_rendered_outputs_preserve_interpretation_guardrails():
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    markdown = Path("docs/REAL_V2_ETHEREUM_RESULTS.md").read_text(encoding="utf-8")
    latex = Path("paper/appendix/real_v2_ethereum_audit.tex").read_text(encoding="utf-8")
    assert "not a causal estimate" in markdown
    assert "Addresses are not natural persons" in markdown
    assert "Causal estimate produced & No" in latex
    assert manifest["raw_log_canonical_sha256"] in markdown
    assert manifest["raw_log_canonical_sha256"] in latex


def test_manuscript_declares_model_theorem_environments():
    manuscript = Path("paper/main.tex").read_text(encoding="utf-8")
    assert r"\newtheorem{proposition}{Proposition}" in manuscript
    assert r"\newtheorem{assumption}{Assumption}" in manuscript
