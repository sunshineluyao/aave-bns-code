import csv
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY = ROOT / "data/metadata/network_measure_glossary.csv"
SUMMARY = ROOT / "outputs/real_v5/core_periphery/summary.json"
DISPLAY_NODES = ROOT / "outputs/real_v5/core_periphery/display_backbone_nodes.csv.gz"
DISPLAY_EDGES = ROOT / "outputs/real_v5/core_periphery/display_backbone_edges.csv.gz"


def test_glossary_is_complete_evidence_aware_and_formula_level():
    with GLOSSARY.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 39
    assert len({row["measure_id"] for row in rows}) == len(rows)
    assert {row["dimension"] for row in rows} == {
        "Participation",
        "Transaction",
        "Structural",
        "Infrastructure",
    }
    assert all(row["formula_latex"] for row in rows)
    assert all(row["text_definition"] for row in rows)
    assert all(row["reference_keys"] for row in rows)
    assert any("blocked" in row["evidence_status"] for row in rows)
    assert any(row["data_available"].startswith("derivable") for row in rows)
    assert any(not row["visualization_path"] for row in rows)


def test_core_periphery_release_keeps_claim_gates_closed():
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert summary["status"] == "audited_address_role_core_periphery_descriptive"
    assert summary["structural_network_result_produced"] is True
    assert summary["entity_level_primary_result_produced"] is False
    assert summary["infrastructure_dependence_result_produced"] is False
    assert summary["causal_estimate_produced"] is False
    assert summary["chains"]["Ethereum"]["maximum_k_core_node_count"] == 14
    assert summary["chains"]["Arbitrum"]["maximum_k_core_node_count"] == 106


def test_display_backbone_is_audited_vector_source_not_estimation_sample():
    nodes = pd.read_csv(DISPLAY_NODES, compression="gzip")
    edges = pd.read_csv(DISPLAY_EDGES, compression="gzip")
    assert nodes.groupby("chain").size().to_dict() == {"Arbitrum": 160, "Ethereum": 160}
    assert set(nodes["observed_unit"]) == {"address_role"}
    assert set(nodes["interpretation_status"]) == {"descriptive_noncausal"}
    assert set(edges["display_status"]) == {"top_weight_backbone_not_estimation_sample"}

    svg = (ROOT / "paper/figures/assets/real_v5_core_periphery.svg").read_text(
        encoding="utf-8"
    )
    assert "<image" not in svg.lower()
    drawio = ROOT / "paper/figures/assets/real_v5_core_periphery.drawio"
    ET.parse(drawio)
    assert "data:image" not in drawio.read_text(encoding="utf-8").lower()


def test_generated_glossary_and_figure_keep_required_disclosures():
    table = (ROOT / "paper/tables/tabA_network_measure_glossary.tex").read_text(
        encoding="utf-8"
    )
    figure = (ROOT / "paper/figures/fig06_real_v5_core_periphery.tex").read_text(
        encoding="utf-8"
    )
    assert r"\label{tab:network-measure-glossary}" in table
    assert r"\AVTableSetup" in table
    assert r"\setstretch{1.02}" in table
    assert r"\setlength{\tabcolsep}{3pt}" in table
    assert r"\resizebox" not in table
    assert r"\scriptsize" not in table
    assert r"\tiny" not in table
    assert r"}\[-1pt]" not in table
    assert "machine-readable" in table
    assert r"\label{fig:real-v5-core-periphery}" in figure
    assert "not verified economic actors" in figure
    assert "All reported statistics use the complete" in figure
