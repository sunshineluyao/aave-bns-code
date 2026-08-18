#!/usr/bin/env python3
# ruff: noqa: E501
"""Render evidence-locked real-v5 manuscript assets.

The source CSVs are committed audited outputs. This renderer intentionally produces
descriptive, address-level assets only; it never opens entity, bridge-route, or causal gates.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from render_scale_adjusted_topology_table import render as render_scale_table

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_PATH = ROOT / "outputs/real_v5/descriptive/weekly_address_metrics.csv"
TOPOLOGY_PATH = ROOT / "outputs/real_v5/topology/address_role_topology_metrics.csv"
CORE_SUMMARY_PATH = ROOT / "outputs/real_v5/core_periphery/summary.json"
FIGURE_TEX = ROOT / "paper/figures/fig05_real_v5_cross_chain.tex"
TABLE_TEX = ROOT / "paper/tables/tab05_real_v5_topology.tex"
ASSET_DIR = ROOT / "paper/figures/assets"
SVG_PATH = ASSET_DIR / "real_v5_cross_chain_descriptive.svg"
DRAWIO_PATH = ASSET_DIR / "real_v5_cross_chain_descriptive.drawio"
MANIFEST_PATH = ASSET_DIR / "real_v5_cross_chain_descriptive_manifest.json"

CHAINS = ("Ethereum", "Arbitrum")
COLORS = {"Ethereum": "#2F6B9A", "Arbitrum": "#C46B1A"}
TIKZ_COLORS = {"Ethereum": "EthereumBlue", "Arbitrum": "ArbitrumOrange"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_data() -> tuple[dict[str, list[dict[str, float]]], dict[str, dict[str, float]]]:
    weekly_raw = read_csv(WEEKLY_PATH)
    grouped: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in weekly_raw:
        if row["chain"] not in CHAINS:
            continue
        if row["causal_status"] != "descriptive_only":
            raise ValueError("real-v5 weekly inputs must remain descriptive_only")
        grouped[row["chain"]].append(
            {
                "week": int(row["event_week"]),
                "active": int(row["active_beneficiary_addresses"]),
                "hhi": float(row["beneficiary_hhi"]),
            }
        )

    expected_weeks = list(range(-16, 17))
    for chain in CHAINS:
        grouped[chain].sort(key=lambda row: row["week"])
        if [row["week"] for row in grouped[chain]] != expected_weeks:
            raise ValueError(f"{chain} must contain exactly event weeks -16 through +16")
        pre_mean = sum(row["active"] for row in grouped[chain] if row["week"] < 0) / 16
        for row in grouped[chain]:
            row["active_index"] = 100.0 * row["active"] / pre_mean

    topology: dict[str, dict[str, float]] = {}
    for row in read_csv(TOPOLOGY_PATH):
        if row["layer"] != "all_actions" or row["chain"] not in CHAINS:
            continue
        if row["interpretation_status"] != "descriptive_noncausal":
            raise ValueError("real-v5 topology inputs must remain descriptive_noncausal")
        topology[row["chain"]] = {
            "events": int(row["event_count"]),
            "self_share": float(row["self_directed_event_share"]),
            "delegated": int(row["delegated_event_count"]),
            "nodes": int(row["topology_node_count"]),
            "edges": int(row["topology_edge_count"]),
            "largest_component": float(row["largest_weak_component_share"]),
            "out_hhi": float(row["weighted_out_degree_hhi"]),
            "in_hhi": float(row["weighted_in_degree_hhi"]),
            "pagerank_hhi": float(row["pagerank_hhi"]),
            "max_core": int(row["maximum_k_core"]),
            "max_core_share": float(row["maximum_core_node_share"]),
        }
    if set(topology) != set(CHAINS):
        raise ValueError("missing all-actions topology row")
    core_summary = json.loads(CORE_SUMMARY_PATH.read_text(encoding="utf-8"))
    if core_summary["status"] != "audited_address_role_core_periphery_descriptive":
        raise ValueError("core-periphery inputs must remain address-role descriptive")
    if core_summary["locked_metrics_sha256"] != sha256(TOPOLOGY_PATH):
        raise ValueError("core-periphery results are not locked to the topology input")
    for chain in CHAINS:
        core = core_summary["chains"][chain]
        if core["evidence_status"] != "observed_address_role_descriptive_noncausal":
            raise ValueError("core-periphery evidence gate changed")
        if core["directed_node_count"] != topology[chain]["nodes"]:
            raise ValueError(f"{chain} core-periphery node count drift")
        if core["directed_edge_count"] != topology[chain]["edges"]:
            raise ValueError(f"{chain} core-periphery edge count drift")
        topology[chain].update(
            {
                "max_core_nodes": int(core["maximum_k_core_node_count"]),
                "be_core_nodes": int(core["borgatti_everett"]["core_node_count"]),
                "be_fit": float(core["borgatti_everett"]["fit_correlation"]),
                "be_k_jaccard": float(
                    core["method_agreement"]["be_maximum_k_core_jaccard"]
                ),
                "rombach_k_spearman": float(
                    core["method_agreement"]["rombach_k_core_spearman"]
                ),
                "persistent_core_nodes": int(
                    core["temporal_core"]["addresses_with_persistence_at_least_half"]
                ),
            }
        )
    return dict(grouped), topology


def map_point(
    week: float,
    value: float,
    *,
    x0: float,
    y0: float,
    width: float,
    height: float,
    ymin: float,
    ymax: float,
) -> tuple[float, float]:
    x = x0 + (week + 16.0) / 32.0 * width
    y = y0 + (value - ymin) / (ymax - ymin) * height
    return x, y


def tikz_points(rows: list[dict[str, float]], field: str, panel: str) -> str:
    if panel == "activity":
        args = dict(x0=0.0, y0=0.0, width=6.0, height=3.6, ymin=50.0, ymax=270.0)
    else:
        args = dict(x0=7.3, y0=0.0, width=6.0, height=3.6, ymin=0.0, ymax=0.035)
    return " ".join(
        f"({map_point(row['week'], row[field], **args)[0]:.4f},"
        f"{map_point(row['week'], row[field], **args)[1]:.4f})"
        for row in rows
    )


def render_tikz(weekly: dict[str, list[dict[str, float]]]) -> str:
    eth_activity = tikz_points(weekly["Ethereum"], "active_index", "activity")
    arb_activity = tikz_points(weekly["Arbitrum"], "active_index", "activity")
    eth_hhi = tikz_points(weekly["Ethereum"], "hhi", "hhi")
    arb_hhi = tikz_points(weekly["Arbitrum"], "hhi", "hhi")
    return rf"""% Generated by scripts/render_real_v5_paper_assets.py; do not edit by hand.
\begin{{figure}}[htbp]
\centering
\suspendrefereelines
\definecolor{{EthereumBlue}}{{HTML}}{{2F6B9A}}
\definecolor{{ArbitrumOrange}}{{HTML}}{{C46B1A}}
\definecolor{{GridGray}}{{HTML}}{{C8D2DC}}
\begin{{tikzpicture}}[x=.89cm,y=1cm,font=\AVFigureFont]
  \path[use as bounding box] (-.35,-1.38) rectangle (13.65,5.72);
  % Protected legend band: the legend is neither overlaid on the axes nor
  % placed on the same baseline as the panel titles.
  \draw[EthereumBlue,av/legend sample] (4.05,5.40)--(4.70,5.40);
  \node[av/legend label] at (4.90,5.40) {{Ethereum}};
  \draw[ArbitrumOrange,av/legend sample,densely dashed] (6.72,5.40)--(7.37,5.40);
  \node[av/legend label] at (7.57,5.40) {{Arbitrum}};
  \node[anchor=center,align=center,font=\AVFigureTitleFont,text width=46mm] at (3,4.55) {{A. Participation index}};
  \node[anchor=west,text=gray!75!black] at (0,4.12) {{Pre-period mean = 100}};
  \node[anchor=center,align=center,font=\AVFigureTitleFont,text width=46mm] at (10.3,4.55) {{B. Pool-event HHI}};
  \draw[GridGray] (0,0) rectangle (6,3.6);
  \draw[GridGray] (7.3,0) rectangle (13.3,3.6);
  \foreach \y/\label in {{0/50,0.8182/100,1.6364/150,2.4545/200,3.2727/250}}{{
    \draw[GridGray!70] (0,\y)--(6,\y);
    \node[anchor=east] at (-0.10,\y) {{\label}};
  }}
  \foreach \y/\label in {{0/0.00,1.0286/0.01,2.0571/0.02,3.0857/0.03}}{{
    \draw[GridGray!70] (7.3,\y)--(13.3,\y);
    \node[anchor=east] at (7.20,\y) {{\label}};
  }}
  \draw[gray!70,densely dashed] (0,0.8182)--(6,0.8182);
  \draw[gray!65,densely dashed] (3,0)--(3,3.6);
  \draw[gray!65,densely dashed] (10.3,0)--(10.3,3.6);
  \draw[EthereumBlue,line width=1.30pt] plot coordinates {{{eth_activity}}};
  \draw[ArbitrumOrange,line width=1.30pt,densely dashed] plot coordinates {{{arb_activity}}};
  \draw[EthereumBlue,line width=1.30pt] plot coordinates {{{eth_hhi}}};
  \draw[ArbitrumOrange,line width=1.30pt,densely dashed] plot coordinates {{{arb_hhi}}};
  \foreach \x/\label in {{0/-16,1.5/-8,3/0,4.5/+8,6/+16}}{{
    \draw (\x,0)--(\x,-0.08) node[below=0.8mm]{{\label}};
  }}
  \foreach \x/\label in {{7.3/-16,8.8/-8,10.3/0,11.8/+8,13.3/+16}}{{
    \draw (\x,0)--(\x,-0.08) node[below=0.8mm]{{\label}};
  }}
  \node at (3,-0.55) {{Event week relative to chain-specific GHO activation}};
  \node at (10.3,-0.55) {{Event week relative to chain-specific GHO activation}};
  \node[anchor=west,text=gray!75!black] at (0,-1.05) {{Observed address-level measures; vertical dashed lines mark event week 0.}};
\end{{tikzpicture}}
\resumerefereelines
\caption{{Chain-relative comparison of address breadth and Pool-event-frequency concentration.}}
\label{{fig:real-v5-cross-chain}}
\begin{{minipage}}{{0.94\linewidth}}
\AVFigureSmallFont\emph{{Notes:}} Event weeks are relative to each chain's own verified GHO reserve activation, so the lines compare different calendar periods and are not a treatment comparison. The activity index divides each chain's weekly active position-holder-address count by its own mean over weeks $-16$ to $-1$. HHI uses equally weighted Pool-event counts assigned to position-holder addresses. All actions and reserves are aggregated; marks are descriptive, addresses are not verified actors, and no GHO-specific effect is shown.
\end{{minipage}}
\end{{figure}}
"""


def render_table(topology: dict[str, dict[str, float]]) -> str:
    eth, arb = topology["Ethereum"], topology["Arbitrum"]
    scale_rows = [
        ("Pool events", f"{eth['events']:,.0f}", f"{arb['events']:,.0f}"),
        ("Self-directed event share", f"{100 * eth['self_share']:.2f}\\%", f"{100 * arb['self_share']:.2f}\\%"),
        ("Delegated or third-party events", f"{eth['delegated']:,.0f}", f"{arb['delegated']:,.0f}"),
    ]
    topology_rows = [
        ("Role-network nodes", f"{eth['nodes']:,.0f}", f"{arb['nodes']:,.0f}"),
        ("Unique directed edges", f"{eth['edges']:,.0f}", f"{arb['edges']:,.0f}"),
        ("Largest weak component", f"{100 * eth['largest_component']:.2f}\\%", f"{100 * arb['largest_component']:.2f}\\%"),
        ("Weighted out-degree HHI", f"{eth['out_hhi']:.4f}", f"{arb['out_hhi']:.4f}"),
        ("Weighted in-degree HHI", f"{eth['in_hhi']:.4f}", f"{arb['in_hhi']:.4f}"),
        ("PageRank HHI", f"{eth['pagerank_hhi']:.6f}", f"{arb['pagerank_hhi']:.6f}"),
        (
            "Maximum $k$-core (nodes)",
            f"{eth['max_core']:.0f} ({eth['max_core_nodes']:.0f})",
            f"{arb['max_core']:.0f} ({arb['max_core_nodes']:.0f})",
        ),
        ("BE binary-core nodes", f"{eth['be_core_nodes']:.0f}", f"{arb['be_core_nodes']:.0f}"),
        ("BE ideal-matrix correlation", f"{eth['be_fit']:.3f}", f"{arb['be_fit']:.3f}"),
        ("BE--maximum-$k$ Jaccard", f"{eth['be_k_jaccard']:.3f}", f"{arb['be_k_jaccard']:.3f}"),
        (
            r"Rombach--$k$-core Spearman $\rho$",
            f"{eth['rombach_k_spearman']:.3f}",
            f"{arb['rombach_k_spearman']:.3f}",
        ),
        (
            r"Weekly maximum-core persistence $\geq 0.5$",
            f"{eth['persistent_core_nodes']:.0f}",
            f"{arb['persistent_core_nodes']:.0f}",
        ),
    ]
    scale_body = "\n".join(
        f"{label} & {left} & {right} \\\\" for label, left, right in scale_rows
    )
    topology_body = "\n".join(
        f"{label} & {left} & {right} \\\\" for label, left, right in topology_rows
    )
    return rf"""% Generated by scripts/render_real_v5_paper_assets.py; do not edit by hand.
\begin{{table}}[htbp]
\centering
\caption{{Audited full-window address-role topology.}}
\label{{tab:real-v5-topology}}
\AVTableSetup
\begin{{tabularx}}{{0.90\linewidth}}{{Xrr}}
\toprule
\AVTableHeader
\textbf{{Measure}} & \textbf{{Ethereum}} & \textbf{{Arbitrum}} \\
\midrule
\AVTableSubheader
\multicolumn{{3}}{{l}}{{\emph{{Scale and role assignment}}}} \\
{scale_body}
\addlinespace[2pt]
\AVTableSubheader
\multicolumn{{3}}{{l}}{{\emph{{Topology and concentration}}}} \\
{topology_body}
\bottomrule
\end{{tabularx}}
\begin{{tablenotes}}[flushleft]
\AVTableNoteFont
\item \emph{{Notes:}} A directed edge runs from transaction actor to position-holder address and is weighted by Pool-event count. Self-directed events are reported but excluded from topology; core methods use the weighted undirected projection. The Borgatti--Everett (BE) binary fit requires at least two core nodes. Rombach coreness aggregates nine locked $(\alpha,\beta)$ profiles. Persistence is the share of active weeks in which an address belongs to that week's maximum $k$-core. Method disagreement is a result, not a failed robustness check. Results are descriptive and address-role based, not entity-level or causal.
\end{{tablenotes}}
\end{{table}}
"""


def svg_polyline(
    rows: list[dict[str, float]],
    field: str,
    *,
    panel: str,
    chain: str,
) -> str:
    if panel == "activity":
        args = dict(x0=90.0, y0=500.0, width=540.0, height=-330.0, ymin=50.0, ymax=270.0)
    else:
        args = dict(x0=760.0, y0=500.0, width=540.0, height=-330.0, ymin=0.0, ymax=0.035)
    points = " ".join(
        f"{map_point(row['week'], row[field], **args)[0]:.2f},"
        f"{map_point(row['week'], row[field], **args)[1]:.2f}"
        for row in rows
    )
    dash = ' stroke-dasharray="11 7"' if chain == "Arbitrum" else ""
    return (
        f'<polyline points="{points}" fill="none" stroke="{COLORS[chain]}" '
        f'stroke-width="4" stroke-linejoin="round" stroke-linecap="round"{dash}/>'
    )


def render_svg(weekly: dict[str, list[dict[str, float]]]) -> str:
    x_ticks = [(-16, "-16"), (-8, "-8"), (0, "0"), (8, "+8"), (16, "+16")]
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="640" viewBox="0 0 1400 640" role="img" aria-labelledby="title desc">',
        '<title id="title">Cross-chain event-time activity breadth and concentration</title>',
        '<desc id="desc">Descriptive Ethereum and Arbitrum address-level measures over event weeks minus sixteen through plus sixteen.</desc>',
        '<rect width="1400" height="640" fill="#FFFFFF"/>',
        '<g font-family="Latin Modern Roman, serif" fill="#17324D">',
        '<text x="360" y="74" text-anchor="middle" font-size="24" font-weight="700">A. Participation index</text>',
        '<text x="90" y="108" font-size="18" fill="#5D6B78">Pre-period mean = 100</text>',
        '<text x="1030" y="74" text-anchor="middle" font-size="24" font-weight="700">B. Pool-event HHI</text>',
        '<rect x="90" y="170" width="540" height="330" fill="#FFFFFF" stroke="#C8D2DC" stroke-width="2"/>',
        '<rect x="760" y="170" width="540" height="330" fill="#FFFFFF" stroke="#C8D2DC" stroke-width="2"/>',
    ]
    for value in (50, 100, 150, 200, 250):
        _, y = map_point(-16, value, x0=90, y0=500, width=540, height=-330, ymin=50, ymax=270)
        pieces.append(f'<line x1="90" y1="{y:.2f}" x2="630" y2="{y:.2f}" stroke="#DCE3EA"/>')
        pieces.append(f'<text x="76" y="{y + 6:.2f}" text-anchor="end" font-size="17">{value}</text>')
    for value in (0.00, 0.01, 0.02, 0.03):
        _, y = map_point(-16, value, x0=760, y0=500, width=540, height=-330, ymin=0, ymax=0.035)
        pieces.append(f'<line x1="760" y1="{y:.2f}" x2="1300" y2="{y:.2f}" stroke="#DCE3EA"/>')
        pieces.append(f'<text x="746" y="{y + 6:.2f}" text-anchor="end" font-size="17">{value:.2f}</text>')
    _, baseline_y = map_point(-16, 100, x0=90, y0=500, width=540, height=-330, ymin=50, ymax=270)
    pieces.append(f'<line x1="90" y1="{baseline_y:.2f}" x2="630" y2="{baseline_y:.2f}" stroke="#5D6B78" stroke-dasharray="7 6"/>')
    for x0 in (90, 760):
        event_x = x0 + 270
        pieces.append(f'<line x1="{event_x}" y1="170" x2="{event_x}" y2="500" stroke="#5D6B78" stroke-dasharray="7 6"/>')
        for week, label in x_ticks:
            x = x0 + (week + 16) / 32 * 540
            pieces.append(f'<line x1="{x:.2f}" y1="500" x2="{x:.2f}" y2="508" stroke="#17324D"/>')
            pieces.append(f'<text x="{x:.2f}" y="533" text-anchor="middle" font-size="17">{html.escape(label)}</text>')
    pieces.extend(
        [
            svg_polyline(weekly["Ethereum"], "active_index", panel="activity", chain="Ethereum"),
            svg_polyline(weekly["Arbitrum"], "active_index", panel="activity", chain="Arbitrum"),
            svg_polyline(weekly["Ethereum"], "hhi", panel="hhi", chain="Ethereum"),
            svg_polyline(weekly["Arbitrum"], "hhi", panel="hhi", chain="Arbitrum"),
            f'<line x1="485" y1="126" x2="545" y2="126" stroke="{COLORS["Ethereum"]}" stroke-width="4"/>',
            '<text x="557" y="132" font-size="19">Ethereum</text>',
            f'<line x1="675" y1="126" x2="735" y2="126" stroke="{COLORS["Arbitrum"]}" stroke-width="4" stroke-dasharray="11 7"/>',
            '<text x="747" y="132" font-size="19">Arbitrum</text>',
            '<text x="360" y="570" text-anchor="middle" font-size="18">Event week relative to chain-specific GHO activation</text>',
            '<text x="1030" y="570" text-anchor="middle" font-size="18">Event week relative to chain-specific GHO activation</text>',
            '<text x="90" y="615" font-size="17" fill="#5D6B78">Descriptive address-level measures; event weeks are chain-relative and do not identify causal effects.</text>',
            '</g></svg>',
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

    def vertex(self, value: str, x: float, y: float, width: float, height: float, style: str) -> None:
        cell_id = self._id()
        self.cells.append(
            f'<mxCell id="{cell_id}" value="{html.escape(value)}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" as="geometry"/>'
            "</mxCell>"
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


def render_drawio(weekly: dict[str, list[dict[str, float]]]) -> str:
    b = DrawioBuilder()
    text_style = "text;html=0;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontFamily=Latin Modern Roman;fontColor=#17324D;"
    center_style = text_style.replace("align=left", "align=center")
    b.vertex("A. Participation index", 115, 35, 490, 40, center_style + "fontSize=22;fontStyle=1;")
    b.vertex("Pre-period mean = 100", 90, 76, 250, 30, text_style + "fontSize=17;fontColor=#5D6B78;")
    b.vertex("B. Pool-event HHI", 785, 35, 490, 40, center_style + "fontSize=22;fontStyle=1;")
    b.vertex("", 90, 170, 540, 330, "rounded=0;whiteSpace=wrap;html=0;fillColor=#FFFFFF;strokeColor=#C8D2DC;strokeWidth=2;")
    b.vertex("", 760, 170, 540, 330, "rounded=0;whiteSpace=wrap;html=0;fillColor=#FFFFFF;strokeColor=#C8D2DC;strokeWidth=2;")
    grid_style = "endArrow=none;startArrow=none;strokeColor=#DCE3EA;strokeWidth=1;"
    axis_style = "endArrow=none;startArrow=none;strokeColor=#17324D;strokeWidth=1;"
    for value in (50, 100, 150, 200, 250):
        _, y = map_point(-16, value, x0=90, y0=500, width=540, height=-330, ymin=50, ymax=270)
        b.edge(90, y, 630, y, grid_style)
        b.vertex(str(value), 35, y - 14, 45, 28, center_style + "fontSize=16;")
    for value in (0.00, 0.01, 0.02, 0.03):
        _, y = map_point(-16, value, x0=760, y0=500, width=540, height=-330, ymin=0, ymax=0.035)
        b.edge(760, y, 1300, y, grid_style)
        b.vertex(f"{value:.2f}", 700, y - 14, 50, 28, center_style + "fontSize=16;")
    _, baseline_y = map_point(-16, 100, x0=90, y0=500, width=540, height=-330, ymin=50, ymax=270)
    b.edge(90, baseline_y, 630, baseline_y, "endArrow=none;startArrow=none;strokeColor=#5D6B78;dashed=1;dashPattern=7 6;")
    for x0 in (90, 760):
        event_x = x0 + 270
        b.edge(event_x, 170, event_x, 500, "endArrow=none;startArrow=none;strokeColor=#5D6B78;dashed=1;dashPattern=7 6;")
        for week, label in ((-16, "-16"), (-8, "-8"), (0, "0"), (8, "+8"), (16, "+16")):
            x = x0 + (week + 16) / 32 * 540
            b.edge(x, 500, x, 508, axis_style)
            b.vertex(label, x - 25, 510, 50, 28, center_style + "fontSize=16;")
    for panel, field, args in (
        ("activity", "active_index", dict(x0=90, y0=500, width=540, height=-330, ymin=50, ymax=270)),
        ("hhi", "hhi", dict(x0=760, y0=500, width=540, height=-330, ymin=0, ymax=0.035)),
    ):
        del panel
        for chain in CHAINS:
            points = [map_point(row["week"], row[field], **args) for row in weekly[chain]]
            style = f"endArrow=none;startArrow=none;strokeColor={COLORS[chain]};strokeWidth=3;rounded=1;"
            if chain == "Arbitrum":
                style += "dashed=1;dashPattern=11 7;"
            for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
                b.edge(x1, y1, x2, y2, style)
    b.edge(485, 126, 545, 126, f"endArrow=none;startArrow=none;strokeColor={COLORS['Ethereum']};strokeWidth=3;")
    b.vertex("Ethereum", 555, 108, 100, 36, text_style + "fontSize=18;")
    b.edge(675, 126, 735, 126, f"endArrow=none;startArrow=none;strokeColor={COLORS['Arbitrum']};strokeWidth=3;dashed=1;dashPattern=11 7;")
    b.vertex("Arbitrum", 745, 108, 100, 36, text_style + "fontSize=18;")
    b.vertex("Event week relative to chain-specific GHO activation", 150, 545, 420, 32, center_style + "fontSize=17;")
    b.vertex("Event week relative to chain-specific GHO activation", 820, 545, 420, 32, center_style + "fontSize=17;")
    b.vertex("Descriptive address-level measures; event weeks are chain-relative and do not identify causal effects.", 90, 590, 1000, 32, text_style + "fontSize=16;fontColor=#5D6B78;")
    cells = "".join(b.cells)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxfile host="app.diagrams.net" agent="aave-bns" version="24.7.17" '
        'data-source="outputs/real_v5/descriptive/weekly_address_metrics.csv">'
        '<diagram id="real-v5-cross-chain" name="Figure 5">'
        '<mxGraphModel dx="1400" dy="640" grid="1" gridSize="10" guides="1" tooltips="1" '
        'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1400" pageHeight="640" '
        'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}</root></mxGraphModel></diagram></mxfile>\n"
    )


def write_outputs() -> None:
    weekly, topology = load_data()
    FIGURE_TEX.parent.mkdir(parents=True, exist_ok=True)
    TABLE_TEX.parent.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_TEX.write_text(render_tikz(weekly), encoding="utf-8")
    core_summary = json.loads(CORE_SUMMARY_PATH.read_text(encoding="utf-8"))
    TABLE_TEX.write_text(render_scale_table(core_summary), encoding="utf-8")
    SVG_PATH.write_text(render_svg(weekly), encoding="utf-8")
    DRAWIO_PATH.write_text(render_drawio(weekly), encoding="utf-8")

    ET.parse(DRAWIO_PATH)
    svg = SVG_PATH.read_text(encoding="utf-8")
    if "<image" in svg.lower():
        raise ValueError("SVG must remain vector-only")
    pre_post = {}
    for chain in CHAINS:
        pre = [row for row in weekly[chain] if row["week"] < 0]
        post = [row for row in weekly[chain] if row["week"] > 0]
        active_pre = sum(row["active"] for row in pre) / len(pre)
        active_post = sum(row["active"] for row in post) / len(post)
        hhi_pre = sum(row["hhi"] for row in pre) / len(pre)
        hhi_post = sum(row["hhi"] for row in post) / len(post)
        pre_post[chain] = {
            "active_beneficiary_addresses_pre_mean": active_pre,
            "active_beneficiary_addresses_post_mean": active_post,
            "active_beneficiary_addresses_percent_change": 100 * (active_post / active_pre - 1),
            "beneficiary_hhi_pre_mean": hhi_pre,
            "beneficiary_hhi_post_mean": hhi_post,
            "beneficiary_hhi_percent_change": 100 * (hhi_post / hhi_pre - 1),
        }
    manifest = {
        "schema_version": 1,
        "evidence_status": "audited_address_level_descriptive_noncausal",
        "inputs": {
            str(WEEKLY_PATH.relative_to(ROOT)): sha256(WEEKLY_PATH),
            str(TOPOLOGY_PATH.relative_to(ROOT)): sha256(TOPOLOGY_PATH),
            str(CORE_SUMMARY_PATH.relative_to(ROOT)): sha256(CORE_SUMMARY_PATH),
        },
        "outputs": {
            str(FIGURE_TEX.relative_to(ROOT)): sha256(FIGURE_TEX),
            str(TABLE_TEX.relative_to(ROOT)): sha256(TABLE_TEX),
            str(SVG_PATH.relative_to(ROOT)): sha256(SVG_PATH),
            str(DRAWIO_PATH.relative_to(ROOT)): sha256(DRAWIO_PATH),
        },
        "claim_gates": {
            "causal_estimate_produced": False,
            "entity_level_primary_result_produced": False,
            "infrastructure_dependence_result_produced": False,
        },
        "descriptive_pre_post": pre_post,
        "topology_headline": {
            chain: {
                "weighted_out_degree_hhi": topology[chain]["out_hhi"],
                "weighted_in_degree_hhi": topology[chain]["in_hhi"],
                "maximum_k_core": topology[chain]["max_core"],
                "maximum_k_core_node_count": topology[chain]["max_core_nodes"],
                "borgatti_everett_core_node_count": topology[chain]["be_core_nodes"],
                "borgatti_everett_fit_correlation": topology[chain]["be_fit"],
                "be_maximum_k_core_jaccard": topology[chain]["be_k_jaccard"],
                "rombach_k_core_spearman": topology[chain]["rombach_k_spearman"],
                "weekly_core_persistence_at_least_half": topology[chain][
                    "persistent_core_nodes"
                ],
            }
            for chain in CHAINS
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(FIGURE_TEX)
    print(TABLE_TEX)
    print(SVG_PATH)
    print(DRAWIO_PATH)
    print(MANIFEST_PATH)


if __name__ == "__main__":
    write_outputs()
