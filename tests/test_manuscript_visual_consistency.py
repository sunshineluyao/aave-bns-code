from __future__ import annotations

import csv
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_active_figures_use_the_shared_roman_type_system_without_geometric_scaling() -> None:
    active = [
        "fig01_research_design.tex",
        "fig02_institutional_timeline.tex",
        "fig03_simulation_design.tex",
        "fig03b_simulation_outcomes.tex",
        "fig04_tx_call_log_network.tex",
        "fig05_real_v5_cross_chain.tex",
        "fig03_real_v3_measurement.tex",
        "fig04_real_v4_partial_identification.tex",
        "fig06_real_v5_core_periphery.tex",
        "fig07_arbitrum_gnosis_benchmark.tex",
    ]
    forbidden = (r"\sffamily", r"font=\sffamily", r"\resizebox", r"\tiny")
    for name in active:
        source = read(PAPER / "figures" / name)
        assert r"\begin{tikzpicture}" in source
        assert not any(token in source for token in forbidden)


def test_conceptual_figures_use_original_semantic_line_icons() -> None:
    conceptual = (
        "fig01_research_design.tex",
        "fig02_institutional_timeline.tex",
        "fig03_simulation_design.tex",
        "fig04_tx_call_log_network.tex",
    )
    for name in conceptual:
        assert r"\pic[av/icon" in read(PAPER / "figures" / name)

    style = read(PAPER / "visual_style.tex")
    for icon in (
        "blocks",
        "database",
        "document",
        "network",
        "chart",
        "filter",
        "users",
        "bridge",
        "code",
        "hash",
        "clock",
        "coin",
    ):
        assert f"pics/av/{icon}/.style" in style


def test_active_figures_reserve_protected_title_legend_and_caption_geometry() -> None:
    style = read(PAPER / "visual_style.tex")
    assert "av/panel title/.style" in style
    assert "av/legend label/.style" in style
    assert "av/legend sample/.style" in style

    research_design = read(PAPER / "figures" / "fig01_research_design.tex")
    mapping = read(PAPER / "figures" / "fig04_tx_call_log_network.tex")
    cross_chain = read(PAPER / "figures" / "fig05_real_v5_cross_chain.tex")
    core = read(PAPER / "figures" / "fig06_real_v5_core_periphery.tex")
    assert "protected icon band" in research_design.lower()
    assert "Protected icon bands" in mapping
    assert "Protected legend band" in cross_chain
    assert "protected viewport" in core
    assert "Rombach" in core


def test_rendered_pdf_layout_auditor_is_wired_into_release_workflow() -> None:
    auditor = read(ROOT / "scripts" / "audit_pdf_layout.py")
    workflow = read(ROOT / ".github" / "workflows" / "paper.yml")
    makefile = read(ROOT / "Makefile")
    for contract in (
        "stroke_text_collision",
        "fill_text_collision",
        "text_overlap",
        "caption_gap",
        "_figure_scopes",
        "DEFAULT_MIN_FONT_SIZE = 7.0",
        "--figure-regions",
        "--self-test",
    ):
        assert contract in auditor
    assert "Audit rendered figure geometry" in workflow
    assert "4,10,27,29,32,37,63,65,67,68" in workflow
    assert "4,10,27,29,32,37,63,65,67,68" in makefile
    assert "paper-layout-audit" in makefile


def test_tables_use_one_absolute_font_setup() -> None:
    tables = sorted((PAPER / "tables").glob("*.tex"))
    assert tables
    for path in tables:
        source = read(path)
        assert r"\AVTableSetup" in source
        assert r"\resizebox" not in source
        assert r"\tiny" not in source
        assert r"\scriptsize" not in source
        assert r"\footnotesize" not in source

    style = read(PAPER / "visual_style.tex")
    assert r"\fontsize{9}{11}\selectfont" in style
    assert r"\fontsize{8}{9.4}\selectfont" in style


def test_equation_one_and_glossary_use_compact_symbolic_notation() -> None:
    introduction = read(PAPER / "sections" / "01_introduction.tex")
    assert (
        r"\boldsymbol{\Delta}_t="
        r"\left(\mathcal P_t,\mathcal A_t,\mathcal S_t,\mathcal I_t\right)"
    ) in introduction
    assert "denote participation, activity, structural, and infrastructure" in introduction

    with (ROOT / "data/metadata/network_measure_glossary.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        formula = row["formula_latex"]
        assert r"\text{" not in formula
        assert r"\mathrm{" not in formula
        assert r"\operatorname{" not in formula


def test_editable_vector_masters_use_roman_fonts_and_contain_no_raster_payloads() -> None:
    masters = sorted((PAPER / "figures" / "assets").glob("*.drawio"))
    masters.extend(sorted((PAPER / "figures" / "assets").glob("*.svg")))
    masters.extend(sorted((ROOT / "docs" / "figures").glob("*.svg")))
    assert masters
    for path in masters:
        source = read(path)
        lowered = source.lower()
        assert "arial" not in lowered
        assert "helvetica" not in lowered
        assert "sans-serif" not in lowered
        assert "data:image" not in lowered
        assert "<image" not in lowered
        if path.suffix == ".drawio":
            ET.parse(path)
