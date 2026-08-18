from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def manuscript_source_text() -> str:
    paths = [PAPER / "main.tex"]
    for directory in ("sections", "appendix", "tables", "figures"):
        paths.extend(sorted((PAPER / directory).glob("*.tex")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_anonymous_review_source_has_no_direct_identity_leaks() -> None:
    main = read("paper/main.tex")
    manuscript = manuscript_source_text()

    assert "pdfauthor={Anonymous Authors}" in main
    assert r"\fnm{Anonymous} \sur{Authors}" in main
    assert "Affiliations withheld for anonymous review" in main
    assert "Acknowledgements are withheld for anonymous review" in main
    assert "Funding information is withheld for anonymous review" in main
    assert "Author-contribution information is withheld for anonymous review" in main
    assert "Identifying repository and continuous-integration locations are withheld" in main

    forbidden = (
        "luyao.zhang@",
        "Duke Kunshan University",
        "Northwestern University",
        "Cornell University",
        "github.com/sunshineluyao/aave-bns",
        r"\fnm{Ziqiao}",
        r"\fnm{Lin William}",
        r"\fnm{Gergely}",
        r"\fnm{Luyao}",
    )
    for identity in forbidden:
        assert identity not in manuscript


def test_anonymous_release_metadata_has_no_identifying_repository_location() -> None:
    text_paths = [
        ROOT / "outputs/real_v6/artifact_provenance.json",
        ROOT / "outputs/causal_v2/evidence_status_2026-08-11.json",
        ROOT / "docs/REAL_V6_GNOSIS_DID_MVP.md",
        ROOT / "paper/RELEASE_GATE_AUDIT_2026-08-11.md",
        ROOT / "paper/STATUS.md",
    ]
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in text_paths if path.is_file()
    )
    assert "github.com/sunshineluyao/aave-bns" not in combined
    assert "actions/runs/" not in combined


def test_main_uses_six_sections_and_complete_appendix_sequence() -> None:
    main = read("paper/main.tex")
    sections = re.findall(r"\\input\{sections/([^}]+)\}", main)
    assert sections == [
        "01_introduction",
        "02_background",
        "03_model_simulation",
        "04_data_open_science",
        "05_causal_inference",
        "06_conclusion",
    ]
    appendices = re.findall(r"\\input\{appendix/([^}]+)\}", main)
    assert appendices == [
        "measurement_glossary_and_claim_map",
        "source_audit_tables",
        "real_v2_ethereum_audit",
        "real_v3_entity_audit",
        "real_v4_partial_identification",
        "simulation_sensitivity",
        "longitudinal_network_robustness",
        "arbitrum_gnosis_benchmark_robustness",
        "evidence_status_ledger",
        "subgraph_validation_protocol",
        "future_causal_infrastructure_extensions",
    ]


def test_research_design_figure_has_seven_stages_and_fail_closed_strip() -> None:
    figure = read("paper/figures/fig01_research_design.tex")
    for stage in range(1, 8):
        assert f"{stage}. " in figure
    assert "Longitudinal evidence" in figure
    assert "Candidate-donor audit" in figure
    assert "Open-science claim boundary" in figure
    assert "claim gate or incomplete path" in figure
    assert "address $\\neq$ actor; diagnostic $\\neq$ ATT" in figure
    assert "no average treatment effect (ATT) is shown as an observed result" in figure
    assert figure.count(r"\pic[av/icon") >= 10
    drawio = read("paper/figures/assets/fig01_research_design.drawio")
    assert "image=data:image" not in drawio
    assert "embeddedImage" not in drawio


def test_frozen_benchmark_numbers_and_paper_wording_agree() -> None:
    summary = json.loads(
        read("outputs/real_v6/arbitrum_gnosis_did_mvp/summary.json")
    )
    status = json.loads(read("outputs/causal_v2/evidence_status_2026-08-11.json"))
    hhi = summary["primary_results"]["beneficiary_hhi"]
    participation = summary["primary_results"]["log_active_beneficiary_addresses"]
    assert participation["difference_in_changes_arbitrum_minus_gnosis"] == pytest.approx(
        1.98329528157, abs=1e-12
    )
    assert hhi["difference_in_changes_arbitrum_minus_gnosis"] == pytest.approx(
        -0.0184204147055, abs=1e-14
    )
    assert hhi["arbitrum_change"] > 0
    assert hhi["gnosis_change"] > 0
    assert summary["causal_estimate_produced"] is False
    assert summary["causal_language_permitted"] is False
    assert status["claim_gates"]["causal_estimate_produced"] is False
    assert status["claim_gates"]["causal_language_permitted"] is False

    appendix = read("paper/appendix/arbitrum_gnosis_benchmark_robustness.tex")
    table = read("paper/tables/tab07_arbitrum_gnosis_benchmark.tex")
    assert "1.9833" in appendix
    assert "-0.01842" in appendix
    assert "failed-donor" in appendix
    assert "not policy evidence" in read("paper/sections/05_causal_inference.tex")
    assert "not a confidence interval for a treatment effect" in table
    assert "suggestive benchmark evidence" not in manuscript_source_text()


def test_weekly_and_pooled_hhi_changes_are_not_interchanged() -> None:
    results = read("paper/sections/05_causal_inference.tex")
    robustness = read("paper/appendix/longitudinal_network_robustness.tex")
    assert "Mean weekly position-holder-event HHI" in results
    assert "-31.5\\%" in results
    assert "Pooling events within the pre and post periods" in results
    assert "37.0\\%" in results
    assert "HHI is nonlinear" in results
    assert "pooled-period HHI is not the mean weekly HHI" in robustness


def test_scale_and_actor_boundaries_are_not_overclaimed() -> None:
    results = read("paper/sections/05_causal_inference.tex")
    topology = read("paper/tables/tab05_real_v5_topology.tex")
    actor = read("paper/appendix/real_v4_partial_identification.tex")
    assert "99.41\\%" in results
    assert "98.01\\%" in results
    assert "fully contained" in topology
    assert "no scalar cross-chain structural ranking is claimed" in topology
    assert "conservative outer envelope" in actor
    assert "common cross-period controller partition" in actor


def test_rpc_is_authoritative_and_subgraph_validation_remains_future() -> None:
    config = read("configs/subgraphs.yaml")
    main = read("paper/main.tex")
    assert "authority: future_independent_validation_only" in config
    assert config.count("completed: false") >= 2
    assert "deployment_id: null" in config
    assert "Raw consensus RPC logs are authoritative" in config
    assert "future independent validation layer" in main
    assert "it has not been executed" in main


def test_native_vector_figure_release_manifest_is_complete() -> None:
    manifest = json.loads(
        read("paper/figures/FIGURE_RELEASE_MANIFEST_2026-08-11.json")
    )
    figures = manifest["figures"]
    assert manifest["compiled_pages"] == 82
    assert [figure["number"] for figure in figures] == list(range(1, 11))
    assert len({figure["label"] for figure in figures}) == 10
    assert all(figure["qa"] == "PASS" for figure in figures)
    assert all(figure["caption_self_contained"] for figure in figures)

    for figure in figures:
        source_path = ROOT / figure["source"]
        assert source_path.is_file()
        source = source_path.read_text(encoding="utf-8")
        assert rf"\label{{{figure['label']}}}" in source
        assert r"\caption{" in source
        assert r"\begin{tikzpicture}" in source
        for master in figure["editable_masters"]:
            assert (ROOT / master).is_file()

    aux_path = PAPER / "main.aux"
    if aux_path.is_file():
        aux = aux_path.read_text(encoding="utf-8")
        for figure in figures:
            match = re.search(
                rf"\\newlabel\{{{re.escape(figure['label'])}\}}"
                rf"\{{\{{{figure['number']}\}}\{{(\d+)\}}",
                aux,
            )
            assert match is not None
            assert int(match.group(1)) == figure["page"]
