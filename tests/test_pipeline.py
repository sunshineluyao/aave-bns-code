import json
from pathlib import Path

from aave_bns.pipeline import run_demo


def test_demo_pipeline_is_explicitly_synthetic(tmp_path: Path):
    for relative in [
        "data/metadata",
        "paper/generated/tables",
        "paper/generated/figures",
    ]:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    source = Path("data/metadata/treatment_registry.csv")
    (tmp_path / "data/metadata/treatment_registry.csv").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    outputs = run_demo(tmp_path)
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True
    assert outputs["metrics"].exists()
    assert (tmp_path / "paper/generated/tables/policy_events.tex").exists()
