#!/usr/bin/env python3
"""Render the audited address-role core-periphery comparison.

The committed display backbones are deterministic views of the full fitted graphs.  The
renderer emits the publication TikZ source plus editable SVG and diagrams.net sources; it
does not recompute or reinterpret the network estimates.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs/real_v5/core_periphery"
SUMMARY_PATH = OUTPUT_DIR / "summary.json"
FULL_NODE_PATH = OUTPUT_DIR / "node_coreness.csv.gz"
NODE_PATH = OUTPUT_DIR / "display_backbone_nodes.csv.gz"
EDGE_PATH = OUTPUT_DIR / "display_backbone_edges.csv.gz"
FIGURE_TEX = ROOT / "paper/figures/fig06_real_v5_core_periphery.tex"
ASSET_DIR = ROOT / "paper/figures/assets"
SVG_PATH = ASSET_DIR / "real_v5_core_periphery.svg"
DRAWIO_PATH = ASSET_DIR / "real_v5_core_periphery.drawio"
MANIFEST_PATH = ASSET_DIR / "real_v5_core_periphery_manifest.json"

CHAINS = ("Ethereum", "Arbitrum")
PANEL_X = {"Ethereum": 350.0, "Arbitrum": 1050.0}
PANEL_TIKZ_X = {"Ethereum": 3.35, "Arbitrum": 10.65}
PALETTE = (
    "#DCE6EB",
    "#C8D9DF",
    "#AFC9D1",
    "#8CB6C1",
    "#6D9EAD",
    "#3D8992",
    "#147D78",
)
TIKZ_PALETTE = tuple(f"CoreBin{index}" for index in range(len(PALETTE)))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    if summary["status"] != "audited_address_role_core_periphery_descriptive":
        raise ValueError("core-periphery evidence status is not descriptive")
    if any(
        summary[key]
        for key in (
            "entity_level_primary_result_produced",
            "infrastructure_dependence_result_produced",
            "causal_estimate_produced",
        )
    ):
        raise ValueError("a closed claim gate was opened")
    expected_hashes = {
        FULL_NODE_PATH: summary["node_coreness_sha256"],
        NODE_PATH: summary["display_backbone_node_sha256"],
        EDGE_PATH: summary["display_backbone_edge_sha256"],
    }
    for path, expected in expected_hashes.items():
        if sha256(path) != expected:
            raise ValueError(f"core-periphery input hash drift: {path}")
    nodes = pd.read_csv(NODE_PATH, compression="gzip")
    edges = pd.read_csv(EDGE_PATH, compression="gzip")
    if set(nodes["chain"]) != set(CHAINS) or set(edges["chain"]) != set(CHAINS):
        raise ValueError("both chain panels are required")
    if set(nodes["observed_unit"]) != {"address_role"}:
        raise ValueError("node unit must remain address_role")
    if set(nodes["interpretation_status"]) != {"descriptive_noncausal"}:
        raise ValueError("node interpretation must remain descriptive_noncausal")
    if set(edges["display_status"]) != {"top_weight_backbone_not_estimation_sample"}:
        raise ValueError("display edge status changed")
    for chain in CHAINS:
        chain_nodes = set(nodes.loc[nodes["chain"] == chain, "address"])
        chain_edges = edges.loc[edges["chain"] == chain]
        endpoints = set(chain_edges["source_address"]) | set(chain_edges["target_address"])
        if not endpoints.issubset(chain_nodes):
            raise ValueError(f"{chain} display edge has an unlisted endpoint")
        if len(chain_nodes) != summary["display_backbone"][chain]["display_nodes"]:
            raise ValueError(f"{chain} display-node count drift")
    return nodes, edges, summary


def color_bin(percentile: float) -> int:
    return min(len(PALETTE) - 1, max(0, int(math.floor(percentile * len(PALETTE)))))


def node_radius(pagerank: float, maximum: float) -> float:
    return 4.0 + 7.0 * math.sqrt(pagerank / maximum) if maximum else 4.0


def edge_width(weight: float, maximum: float) -> float:
    return 0.55 + 2.15 * math.log1p(weight) / math.log1p(maximum) if maximum else 0.55


def svg_position(chain: str, x: float, y: float) -> tuple[float, float]:
    return PANEL_X[chain] + 250.0 * x, 390.0 - 215.0 * y


def tikz_position(chain: str, x: float, y: float) -> tuple[float, float]:
    # Keep the graph viewport below the three-line metadata band.  The explicit
    # 1.55-unit height is deliberate: the old 2.00-unit viewport let outer-ring
    # nodes and edges intrude into the header text in the rendered PDF.
    return PANEL_TIKZ_X[chain] + 2.50 * x, 3.05 + 1.55 * y


def nodes_in_draw_order(nodes: pd.DataFrame) -> pd.DataFrame:
    """Order nodes by PageRank with an explicit, stable address tie-breaker."""
    ordered = nodes.assign(_address_sort_key=nodes.index.astype(str))
    ordered = ordered.sort_values("_address_sort_key", kind="mergesort")
    ordered = ordered.sort_values("pagerank", kind="mergesort")
    return ordered.drop(columns="_address_sort_key")


def chain_headline(summary: dict[str, object], chain: str) -> tuple[str, str]:
    values = summary["chains"][chain]
    first = (
        f"Full graph: {values['directed_node_count']:,} nodes; "
        f"max k-core k={values['maximum_k_core']} "
        f"({values['maximum_k_core_node_count']} nodes); BE core: "
        f"{values['borgatti_everett']['core_node_count']}"
    )
    second = (
        f"BE-k-core Jaccard {values['method_agreement']['be_maximum_k_core_jaccard']:.3f}; "
        f"Rombach-k-core Spearman {values['method_agreement']['rombach_k_core_spearman']:.3f}"
    )
    return first, second


def render_svg(
    nodes: pd.DataFrame, edges: pd.DataFrame, summary: dict[str, object]
) -> str:
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="760" '
        'viewBox="0 0 1400 760" role="img" aria-labelledby="title desc">',
        '<title id="title">Observed Ethereum and Arbitrum address-role '
        'core-periphery backbones</title>',
        '<desc id="desc">Two data-driven network panels compare binary Borgatti-Everett cores, '
        'maximum k-cores, continuous Rombach coreness, and PageRank. All estimates use full '
        'graphs; '
        'the rendered edges are a legibility backbone.</desc>',
        '<rect width="1400" height="760" fill="#FFFFFF"/>',
        '<g font-family="Latin Modern Roman, serif" fill="#17324D">',
    ]
    for panel_index, chain in enumerate(CHAINS):
        x0 = 25 if chain == "Ethereum" else 725
        title = f"{chr(65 + panel_index)}. {chain} address-role network"
        first, second = chain_headline(summary, chain)
        pieces.extend(
            [
                f'<rect x="{x0}" y="24" width="650" height="625" rx="18" '
                'fill="#F8FAFC" stroke="#C8D2DC" stroke-width="2"/>',
                f'<text x="{x0 + 25}" y="64" font-size="25" font-weight="700">'
                f"{html.escape(title)}</text>",
                f'<text x="{x0 + 25}" y="94" font-size="16" fill="#5D6B78">'
                f"{html.escape(first)}</text>",
                f'<text x="{x0 + 25}" y="121" font-size="16" fill="#5D6B78">'
                f"{html.escape(second)}</text>",
            ]
        )
        chain_nodes = nodes.loc[nodes["chain"] == chain].set_index("address")
        chain_edges = edges.loc[edges["chain"] == chain]
        maximum_edge = float(chain_edges["weight"].max())
        for row in chain_edges.itertuples(index=False):
            source = chain_nodes.loc[row.source_address]
            target = chain_nodes.loc[row.target_address]
            x1, y1 = svg_position(chain, source.display_x, source.display_y)
            x2, y2 = svg_position(chain, target.display_x, target.display_y)
            width = edge_width(float(row.weight), maximum_edge)
            pieces.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="#80909D" stroke-opacity="0.30" stroke-width="{width:.2f}"/>'
            )
        maximum_rank = float(chain_nodes["pagerank"].max())
        for address, row in nodes_in_draw_order(chain_nodes).iterrows():
            x, y = svg_position(chain, row.display_x, row.display_y)
            radius = node_radius(float(row.pagerank), maximum_rank)
            fill = PALETTE[color_bin(float(row.rombach_percentile))]
            stroke = "#C46B1A" if bool(row.maximum_k_core_member) else "#6E7C88"
            stroke_width = 2.4 if bool(row.maximum_k_core_member) else 0.8
            tooltip = (
                f"{address}; Rombach={row.rombach_coreness:.4f}; "
                f"k-core={int(row.core_number)}; PageRank={row.pagerank:.3g}"
            )
            if bool(row.borgatti_everett_core):
                points = " ".join(
                    (
                        f"{x:.2f},{y - radius - 2:.2f}",
                        f"{x + radius + 2:.2f},{y:.2f}",
                        f"{x:.2f},{y + radius + 2:.2f}",
                        f"{x - radius - 2:.2f},{y:.2f}",
                    )
                )
                pieces.append(
                    f'<polygon points="{points}" fill="#6857C7" stroke="#17324D" '
                    f'stroke-width="2.4"><title>{html.escape(tooltip)}</title></polygon>'
                )
            else:
                pieces.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" '
                    f'stroke="{stroke}" stroke-width="{stroke_width:.2f}">'
                    f"<title>{html.escape(tooltip)}</title></circle>"
                )
    pieces.extend(
        [
            '<text x="35" y="692" font-size="16" fill="#5D6B78">'
            'Rombach coreness rank</text>',
            *[
                f'<rect x="{175 + 27 * index}" y="675" width="28" height="22" fill="{color}"/>'
                for index, color in enumerate(PALETTE)
            ],
            '<text x="175" y="719" font-size="14" fill="#5D6B78">low</text>',
            '<text x="353" y="719" font-size="14" text-anchor="end" fill="#5D6B78">high</text>',
            '<circle cx="455" cy="687" r="8" fill="#DCE6EB" stroke="#C46B1A" stroke-width="3"/>',
            '<text x="472" y="693" font-size="16">maximum k-core</text>',
            '<polygon points="657,675 669,687 657,699 645,687" fill="#6857C7" '
            'stroke="#17324D" stroke-width="2"/>',
            '<text x="678" y="693" font-size="16">BE binary core</text>',
            '<circle cx="875" cy="687" r="5" fill="#8CB6C1" stroke="#6E7C88"/>',
            '<circle cx="902" cy="687" r="11" fill="#8CB6C1" stroke="#6E7C88"/>',
            '<text x="920" y="693" font-size="16">node size = PageRank</text>',
            '<line x1="1120" y1="687" x2="1180" y2="687" stroke="#80909D" '
            'stroke-opacity="0.55" stroke-width="3"/>',
            '<text x="1192" y="693" font-size="16">event-weighted backbone</text>',
            '<text x="700" y="746" text-anchor="middle" font-size="15" fill="#5D6B78">'
            'All metrics use the full graphs; 160 nodes per panel are retained only for legible '
            'display. '
            'Address roles are not verified economic actors.</text>',
            "</g></svg>",
        ]
    )
    return "\n".join(pieces) + "\n"


class DrawioBuilder:
    def __init__(self) -> None:
        self.cells: list[str] = []
        self.counter = 2

    def _id(self) -> str:
        value = str(self.counter)
        self.counter += 1
        return value

    def vertex(
        self,
        value: str,
        x: float,
        y: float,
        width: float,
        height: float,
        style: str,
        **attributes: str,
    ) -> None:
        cell_id = self._id()
        attrs = "".join(
            f' {name}="{html.escape(str(attribute), quote=True)}"'
            for name, attribute in attributes.items()
        )
        self.cells.append(
            f'<mxCell id="{cell_id}" value="{html.escape(value, quote=True)}" '
            f'style="{style}" vertex="1" parent="1"{attrs}>'
            f'<mxGeometry x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" '
            f'height="{height:.2f}" as="geometry"/></mxCell>'
        )

    def edge(self, x1: float, y1: float, x2: float, y2: float, style: str) -> None:
        cell_id = self._id()
        self.cells.append(
            f'<mxCell id="{cell_id}" style="{style}" edge="1" parent="1">'
            '<mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x1:.2f}" y="{y1:.2f}" as="sourcePoint"/>'
            f'<mxPoint x="{x2:.2f}" y="{y2:.2f}" as="targetPoint"/>'
            "</mxGeometry></mxCell>"
        )


def render_drawio(
    nodes: pd.DataFrame, edges: pd.DataFrame, summary: dict[str, object]
) -> str:
    builder = DrawioBuilder()
    text = (
        "text;html=0;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontColor=#17324D;"
    )
    for panel_index, chain in enumerate(CHAINS):
        x0 = 25 if chain == "Ethereum" else 725
        title = f"{chr(65 + panel_index)}. {chain} address-role network"
        first, second = chain_headline(summary, chain)
        builder.vertex(
            "", x0, 24, 650, 625,
            "rounded=1;arcSize=5;whiteSpace=wrap;html=0;fillColor=#F8FAFC;"
            "strokeColor=#C8D2DC;strokeWidth=2;",
        )
        builder.vertex(title, x0 + 25, 35, 570, 38, text + "fontSize=23;fontStyle=1;")
        builder.vertex(first, x0 + 25, 75, 600, 28, text + "fontSize=15;fontColor=#5D6B78;")
        builder.vertex(second, x0 + 25, 102, 600, 28, text + "fontSize=15;fontColor=#5D6B78;")
        chain_nodes = nodes.loc[nodes["chain"] == chain].set_index("address")
        chain_edges = edges.loc[edges["chain"] == chain]
        maximum_edge = float(chain_edges["weight"].max())
        for row in chain_edges.itertuples(index=False):
            source = chain_nodes.loc[row.source_address]
            target = chain_nodes.loc[row.target_address]
            x1, y1 = svg_position(chain, source.display_x, source.display_y)
            x2, y2 = svg_position(chain, target.display_x, target.display_y)
            width = edge_width(float(row.weight), maximum_edge)
            builder.edge(
                x1, y1, x2, y2,
                f"endArrow=none;startArrow=none;strokeColor=#80909D;opacity=30;"
                f"strokeWidth={width:.2f};",
            )
        maximum_rank = float(chain_nodes["pagerank"].max())
        for address, row in nodes_in_draw_order(chain_nodes).iterrows():
            x, y = svg_position(chain, row.display_x, row.display_y)
            radius = node_radius(float(row.pagerank), maximum_rank)
            fill = PALETTE[color_bin(float(row.rombach_percentile))]
            if bool(row.borgatti_everett_core):
                style = (
                    "shape=rhombus;whiteSpace=wrap;html=0;fillColor=#6857C7;"
                    "strokeColor=#17324D;strokeWidth=2.4;"
                )
                radius += 2
            else:
                stroke = "#C46B1A" if bool(row.maximum_k_core_member) else "#6E7C88"
                width = 2.4 if bool(row.maximum_k_core_member) else 0.8
                style = (
                    f"ellipse;whiteSpace=wrap;html=0;fillColor={fill};strokeColor={stroke};"
                    f"strokeWidth={width:.2f};"
                )
            builder.vertex(
                "", x - radius, y - radius, 2 * radius, 2 * radius, style,
                address=address,
                rombach_coreness=f"{row.rombach_coreness:.12g}",
                core_number=str(int(row.core_number)),
                pagerank=f"{row.pagerank:.12g}",
                evidence_status="observed_address_role_descriptive_noncausal",
            )
    builder.vertex(
        "Rombach coreness rank: low → high",
        35,
        670,
        300,
        34,
        text + "fontSize=15;",
    )
    builder.vertex(
        "orange ring = maximum k-core; diamond = BE binary core; size = PageRank",
        365, 670, 650, 34, text + "fontSize=15;",
    )
    builder.vertex(
        "All metrics use full graphs; the 160-node panels are display backbones only.",
        35, 718, 1000, 28, text + "fontSize=14;fontColor=#5D6B78;",
    )
    cells = "".join(builder.cells)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxfile host="app.diagrams.net" agent="aave-bns" version="24.7.17" '
        'data-source="outputs/real_v5/core_periphery/display_backbone_nodes.csv.gz;'
        'outputs/real_v5/core_periphery/display_backbone_edges.csv.gz">'
        '<diagram id="real-v5-core-periphery" name="Core-periphery evidence">'
        '<mxGraphModel dx="1400" dy="760" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        'pageWidth="1400" pageHeight="760" math="0" shadow="0"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}</root></mxGraphModel></diagram></mxfile>\n"
    )


def tikz_node_command(row: pd.Series, chain: str, maximum_rank: float) -> str:
    x, y = tikz_position(chain, float(row.display_x), float(row.display_y))
    radius_pt = 1.3 + 2.1 * math.sqrt(float(row.pagerank) / maximum_rank)
    if bool(row.borgatti_everett_core):
        return (
            f"  \\node[diamond,draw=AVNavy,fill=AVViolet,inner sep={radius_pt:.2f}pt,"
            f"line width=0.55pt] at ({x:.4f},{y:.4f}) {{}};"
        )
    outline = "AVOrange" if bool(row.maximum_k_core_member) else "AVSlate!55"
    width = "0.55pt" if bool(row.maximum_k_core_member) else "0.20pt"
    color = TIKZ_PALETTE[color_bin(float(row.rombach_percentile))]
    return (
        f"  \\node[circle,draw={outline},fill={color},inner sep={radius_pt:.2f}pt,"
        f"line width={width}] at ({x:.4f},{y:.4f}) {{}};"
    )


def render_tikz(
    nodes: pd.DataFrame, edges: pd.DataFrame, summary: dict[str, object]
) -> str:
    commands = [
        "% Generated by scripts/render_real_v5_core_periphery.py; do not edit by hand.",
        "\\begin{figure}[htbp]",
        "\\centering",
        "\\suspendrefereelines",
    ]
    for index, color in enumerate(PALETTE):
        commands.append(f"\\definecolor{{CoreBin{index}}}{{HTML}}{{{color[1:]}}}")
    commands.extend(
        [
            "\\begin{tikzpicture}[x=.76cm,y=1cm,font=\\AVFigureFont]",
            "  \\path[use as bounding box] (-0.10,-0.92) rectangle (14.10,7.18);",
            "  \\draw[rounded corners=2mm,fill=AVLight,draw=AVRule,line width=0.5pt] "
            "(0.10,1.20) rectangle (6.60,7.05);",
            "  \\draw[rounded corners=2mm,fill=AVLight,draw=AVRule,line width=0.5pt] "
            "(7.40,1.20) rectangle (13.90,7.05);",
        ]
    )
    for panel_index, chain in enumerate(CHAINS):
        center = 3.35 if chain == "Ethereum" else 10.65
        title = f"{chr(65 + panel_index)}. {chain}"
        values = summary["chains"][chain]
        first = (
            f"Full graph: {values['directed_node_count']:,} nodes; "
            f"{values['undirected_edge_count']:,} edges"
        )
        second = (
            f"$k_{{\\max}}={values['maximum_k_core']}$ "
            f"({values['maximum_k_core_node_count']} nodes); "
            f"BE core = {values['borgatti_everett']['core_node_count']}"
        )
        third = (
            f"BE--$k$ Jaccard "
            f"{values['method_agreement']['be_maximum_k_core_jaccard']:.3f}; "
            f"Rombach--$k$ $\\rho="
            f"{values['method_agreement']['rombach_k_core_spearman']:.3f}$"
        )
        commands.extend(
            [
                f"  \\node[anchor=north,align=center,text width=45mm,font=\\AVFigureTitleFont] "
                f"at ({center},6.83) {{{title}}};",
                f"  \\node[anchor=north,align=center,text width=46mm,text=AVSlate,"
                f"font=\\AVFigureSmallFont] at ({center},6.37) "
                f"{{{first.replace('Full graph:', 'Full:')}\\\\{second}\\\\{third}}};",
            ]
        )
        chain_nodes = nodes.loc[nodes["chain"] == chain].set_index("address")
        chain_edges = edges.loc[edges["chain"] == chain]
        maximum_edge = float(chain_edges["weight"].max())
        for row in chain_edges.itertuples(index=False):
            source = chain_nodes.loc[row.source_address]
            target = chain_nodes.loc[row.target_address]
            x1, y1 = tikz_position(chain, source.display_x, source.display_y)
            x2, y2 = tikz_position(chain, target.display_x, target.display_y)
            width = 0.08 + 0.34 * math.log1p(float(row.weight)) / math.log1p(maximum_edge)
            commands.append(
                f"  \\draw[AVSlate,opacity=0.28,line width={width:.3f}pt] "
                f"({x1:.4f},{y1:.4f})--({x2:.4f},{y2:.4f});"
            )
        maximum_rank = float(chain_nodes["pagerank"].max())
        for _, row in nodes_in_draw_order(chain_nodes).iterrows():
            commands.append(tikz_node_command(row, chain, maximum_rank))
    commands.extend(
        [
            "  \\draw[rounded corners=1.5mm,fill=white,draw=AVRule,line width=0.45pt] "
            "(0.10,-0.72) rectangle (13.90,0.92);",
            "  \\node[av/legend label,text=AVSlate] at (0.28,0.52) {Rombach};",
            *[
                f"  \\fill[{name}] ({2.75 + 0.18 * index:.3f},0.40) rectangle "
                f"({2.93 + 0.18 * index:.3f},0.63);"
                for index, name in enumerate(TIKZ_PALETTE)
            ],
            "  \\node[anchor=north,text=AVSlate,font=\\AVFigureSmallFont] at (2.75,0.35) {low};",
            "  \\node[anchor=north,text=AVSlate,font=\\AVFigureSmallFont] at (4.01,0.35) {high};",
            "  \\node[circle,draw=AVOrange,fill=CoreBin1,inner sep=2.2pt,line width=0.6pt] "
            "at (5.30,0.52) {};",
            "  \\node[av/legend label] at (5.55,0.52) {maximum $k$-core};",
            "  \\node[diamond,draw=AVNavy,fill=AVViolet,inner sep=2.0pt,line width=0.6pt] "
            "at (10.18,0.52) {};",
            "  \\node[av/legend label] at (10.48,0.52) {BE binary core};",
            "  \\node[circle,draw=AVSlate!55,fill=CoreBin3,inner sep=1.3pt] at "
            "(0.45,-0.30) {};",
            "  \\node[circle,draw=AVSlate!55,fill=CoreBin3,inner sep=3.2pt] at "
            "(0.75,-0.30) {};",
            "  \\node[av/legend label] at (1.02,-0.30) {node size = PageRank};",
            "  \\draw[AVSlate,opacity=0.55,line width=0.45pt] "
            "(7.05,-0.30)--(7.80,-0.30);",
            "  \\node[av/legend label] at (8.05,-0.30) {event-weighted backbone};",
            "\\end{tikzpicture}",
            "\\resumerefereelines",
            "\\caption{Observed address-role core--periphery backbones.}",
            "\\label{fig:real-v5-core-periphery}",
            "\\begin{minipage}{0.96\\linewidth}",
            "\\AVFigureSmallFont\\emph{Notes:} All reported statistics use the complete "
            "weighted address-role graphs. For legibility, each panel retains all "
            "Borgatti--Everett and maximum-$k$-core nodes in the largest component, "
            "high-coreness context nodes, and the top-weighted induced backbone; the "
            "committed display CSVs record every shown node and edge. Edge direction is "
            "collapsed and reciprocal weights are summed for core fitting. Fill encodes "
            "quality-weighted Rombach coreness, orange outlines mark the maximum $k$-core, "
            "diamonds mark the binary Borgatti--Everett core, and size encodes PageRank. "
            "The deterministic radial layout places the BE core at the center, maximum-$k$ "
            "nodes on the inner rings, high continuous-coreness nodes on the middle ring, "
            "and context nodes on the outer ring. "
            "These are descriptive address-role positions, not verified economic actors, "
            "ownership, governance power, infrastructure dependence, or treatment effects.",
            "\\end{minipage}",
            "\\end{figure}",
            "",
        ]
    )
    return "\n".join(commands)


def write_outputs() -> None:
    nodes, edges, summary = load_inputs()
    FIGURE_TEX.parent.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_TEX.write_text(render_tikz(nodes, edges, summary), encoding="utf-8")
    SVG_PATH.write_text(render_svg(nodes, edges, summary), encoding="utf-8")
    DRAWIO_PATH.write_text(render_drawio(nodes, edges, summary), encoding="utf-8")
    ET.parse(DRAWIO_PATH)
    if "<image" in SVG_PATH.read_text(encoding="utf-8").lower():
        raise ValueError("core-periphery SVG must remain vector-only")
    manifest = {
        "schema_version": 1,
        "evidence_status": "audited_address_role_core_periphery_descriptive_noncausal",
        "inputs": {
            str(SUMMARY_PATH.relative_to(ROOT)): sha256(SUMMARY_PATH),
            str(FULL_NODE_PATH.relative_to(ROOT)): sha256(FULL_NODE_PATH),
            str(NODE_PATH.relative_to(ROOT)): sha256(NODE_PATH),
            str(EDGE_PATH.relative_to(ROOT)): sha256(EDGE_PATH),
        },
        "outputs": {
            str(FIGURE_TEX.relative_to(ROOT)): sha256(FIGURE_TEX),
            str(SVG_PATH.relative_to(ROOT)): sha256(SVG_PATH),
            str(DRAWIO_PATH.relative_to(ROOT)): sha256(DRAWIO_PATH),
        },
        "native_vector_objects": {
            "drawio_cells": len(ET.parse(DRAWIO_PATH).findall(".//mxCell")),
            "embedded_raster_images": 0,
        },
        "display_backbone": summary["display_backbone"],
        "claim_gates": {
            "entity_level_primary_result_produced": False,
            "infrastructure_dependence_result_produced": False,
            "causal_estimate_produced": False,
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for path in (FIGURE_TEX, SVG_PATH, DRAWIO_PATH, MANIFEST_PATH):
        print(path)


if __name__ == "__main__":
    write_outputs()
