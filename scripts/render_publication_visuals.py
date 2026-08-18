#!/usr/bin/env python3
# ruff: noqa: E501
"""Render unified simulation and evidence-bridge assets for the manuscript.

The simulation is deterministic and synthetic. Empirical inputs are audited
address-level descriptive outputs. This script never opens entity, route, or
causal claim gates.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SIMULATION_PATH = ROOT / "outputs/simulation/scenario_results.csv"
EMPIRICAL_PATH = ROOT / "outputs/real_v5/descriptive/weekly_address_metrics.csv"
ASSET_DIR = ROOT / "paper/figures/assets"
OUTCOMES_TEX = ROOT / "paper/figures/fig03b_simulation_outcomes.tex"
BRIDGE_TEX = ROOT / "paper/figures/fig03c_simulation_empirical_comparison.tex"
OUTCOMES_SVG = ASSET_DIR / "simulation_four_scenarios.svg"
OUTCOMES_DRAWIO = ASSET_DIR / "simulation_four_scenarios.drawio"
BRIDGE_SVG = ASSET_DIR / "simulation_empirical_evidence_bridge.svg"
BRIDGE_DRAWIO = ASSET_DIR / "simulation_empirical_evidence_bridge.drawio"
MANIFEST_PATH = ASSET_DIR / "simulation_empirical_visual_manifest.json"

SCENARIOS = (
    "ethereum_aave",
    "ethereum_gho",
    "crosschain_single",
    "crosschain_redundant",
)
LABELS = {
    "ethereum_aave": "S0 · Benchmark",
    "ethereum_gho": "S1 · GHO access",
    "crosschain_single": "S2 · One route",
    "crosschain_redundant": "S3 · Two routes",
}
COLORS = {
    "ethereum_aave": "#2F6B9A",
    "ethereum_gho": "#6857C7",
    "crosschain_single": "#C46B1A",
    "crosschain_redundant": "#147D78",
}
TIKZ_COLORS = {
    "ethereum_aave": "AVBlue",
    "ethereum_gho": "AVViolet",
    "crosschain_single": "AVOrange",
    "crosschain_redundant": "AVTeal",
}
TIKZ_DASHES = {
    "ethereum_aave": "",
    "ethereum_gho": "dashed,",
    "crosschain_single": "dash dot,",
    "crosschain_redundant": "densely dotted,",
}
SVG_DASHES = {
    "ethereum_aave": "",
    "ethereum_gho": ' stroke-dasharray="11 6"',
    "crosschain_single": ' stroke-dasharray="13 5 3 5"',
    "crosschain_redundant": ' stroke-dasharray="3 5"',
}
DRAWIO_DASHES = {
    "ethereum_aave": "",
    "ethereum_gho": "dashed=1;dashPattern=8 4;",
    "crosschain_single": "dashed=1;dashPattern=10 4 2 4;",
    "crosschain_redundant": "dashed=1;dashPattern=2 4;",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_simulation() -> dict[str, list[dict[str, float]]]:
    grouped: dict[str, list[dict[str, float]]] = {scenario: [] for scenario in SCENARIOS}
    for row in read_csv(SIMULATION_PATH):
        scenario = row["scenario_id"]
        if scenario not in grouped:
            continue
        grouped[scenario].append(
            {
                "beta": float(row["beta"]),
                "active_entities": float(row["active_entities"]),
                "total_activity": float(row["total_activity"]),
                "activity_hhi": float(row["activity_hhi"]),
                "chain_hhi": float(row["chain_hhi"]),
                "structural_hhi": float(row["structural_hhi"]),
                "max_route_removal_loss": float(row["max_route_removal_loss"]),
            }
        )
    expected = [0.1, 0.3, 0.5, 0.7]
    for scenario, rows in grouped.items():
        rows.sort(key=lambda value: value["beta"])
        if [row["beta"] for row in rows] != expected:
            raise ValueError(f"{scenario} must contain beta values {expected}")
    baseline = {row["beta"]: row["total_activity"] for row in grouped["ethereum_aave"]}
    for rows in grouped.values():
        for row in rows:
            row["activity_index"] = row["total_activity"] / baseline[row["beta"]]
    return grouped


def empirical_pre_post() -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, float]]] = {}
    for row in read_csv(EMPIRICAL_PATH):
        if row["causal_status"] != "descriptive_only":
            raise ValueError("Empirical comparison inputs must remain descriptive_only")
        grouped.setdefault(row["chain"], []).append(
            {
                "week": float(row["event_week"]),
                "active": float(row["active_beneficiary_addresses"]),
                "hhi": float(row["beneficiary_hhi"]),
            }
        )
    summary: dict[str, dict[str, float]] = {}
    for chain, rows in grouped.items():
        pre = [row for row in rows if row["week"] < 0]
        post = [row for row in rows if row["week"] > 0]
        active_pre = sum(row["active"] for row in pre) / len(pre)
        active_post = sum(row["active"] for row in post) / len(post)
        hhi_pre = sum(row["hhi"] for row in pre) / len(pre)
        hhi_post = sum(row["hhi"] for row in post) / len(post)
        summary[chain] = {
            "active_percent_change": 100 * (active_post / active_pre - 1),
            "hhi_percent_change": 100 * (hhi_post / hhi_pre - 1),
        }
    return summary


def scale(value: float, lower: float, upper: float, span: float) -> float:
    return (value - lower) / (upper - lower) * span


PANELS = (
    ("A. Active entities", "active_entities", 80.0, 200.0, (80.0, 120.0, 160.0, 200.0), "{:.0f}"),
    ("B. Total activity index", "activity_index", 0.8, 3.2, (1.0, 2.0, 3.0), "{:.1f}"),
    ("C. Activity HHI", "activity_hhi", 0.005, 0.018, (0.006, 0.012, 0.018), "{:.3f}"),
    ("D. Maximum route-removal loss", "max_route_removal_loss", 0.0, 0.55, (0.0, 0.25, 0.50), "{:.2f}"),
)


def tikz_panel(
    simulation: dict[str, list[dict[str, float]]],
    title: str,
    metric: str,
    lower: float,
    upper: float,
    ticks: tuple[float, ...],
    tick_format: str,
    x_shift: float,
    y_shift: float,
) -> list[str]:
    width, height = 6.0, 2.7
    lines = [
        rf"\begin{{scope}}[shift={{({x_shift:.1f},{y_shift:.1f})}}]",
        rf"\node[anchor=west,font=\bfseries] at (0,{height + 0.42:.2f}) {{{title}}};",
        rf"\draw[AVRule,fill=AVLight!35] (0,0) rectangle ({width:.1f},{height:.1f});",
    ]
    for tick in ticks:
        y = scale(tick, lower, upper, height)
        lines.extend(
            [
                rf"\draw[AVRule!75] (0,{y:.4f})--({width:.1f},{y:.4f});",
                rf"\node[anchor=east,text=AVSlate] at (-0.10,{y:.4f}) {{{tick_format.format(tick)}}};",
            ]
        )
    for beta in (0.1, 0.3, 0.5, 0.7):
        x = scale(beta, 0.1, 0.7, width)
        lines.extend(
            [
                rf"\draw[AVNavy] ({x:.4f},0)--({x:.4f},-0.08);",
                rf"\node[below,text=AVSlate] at ({x:.4f},-0.14) {{{beta:.1f}}};",
            ]
        )
    for scenario in SCENARIOS:
        points = " ".join(
            f"({scale(row['beta'], 0.1, 0.7, width):.4f},"
            f"{scale(row[metric], lower, upper, height):.4f})"
            for row in simulation[scenario]
        )
        lines.append(
            rf"\draw[{TIKZ_COLORS[scenario]},{TIKZ_DASHES[scenario]}line width=1.25pt] "
            rf"plot coordinates {{{points}}};"
        )
    lines.extend(
        [
            rf"\node[text=AVSlate] at ({width / 2:.1f},-0.63) {{$\beta$ (network complementarity)}};",
            r"\end{scope}",
        ]
    )
    return lines


def render_outcomes_tikz(simulation: dict[str, list[dict[str, float]]]) -> str:
    lines = [
        "% Generated by scripts/render_publication_visuals.py; do not edit by hand.",
        r"\begin{figure*}[t]",
        r"\centering",
        r"\suspendrefereelines",
        r"\begin{tikzpicture}[x=.84cm,y=1cm,font=\AVFigureFont]",
    ]
    # A dedicated two-row legend band prevents long labels from touching the
    # following line sample and keeps the band clear of panel titles.
    legend_positions = ((0.15, 8.60), (7.15, 8.60), (0.15, 8.18), (7.15, 8.18))
    for (x, y), scenario in zip(legend_positions, SCENARIOS, strict=True):
        lines.extend(
            [
                rf"\draw[{TIKZ_COLORS[scenario]},{TIKZ_DASHES[scenario]}line width=1.25pt] "
                rf"({x:.2f},{y:.2f})--({x + 0.65:.2f},{y:.2f});",
                rf"\node[av/legend label] at ({x + 0.85:.2f},{y:.2f}) "
                rf"{{{LABELS[scenario]}}};",
            ]
        )
    for index, panel in enumerate(PANELS):
        x_shift = 0.0 if index % 2 == 0 else 7.15
        y_shift = 4.15 if index < 2 else 0.0
        lines.extend(tikz_panel(simulation, *panel, x_shift, y_shift))
    lines.extend(
        [
            r"\node[anchor=west,text=AVSlate] at (0,-1.05) {Synthetic matched scenarios; lines use distinct color and dash encodings.};",
            r"\end{tikzpicture}",
            r"\resumerefereelines",
            r"\caption{Four-scenario simulation outcomes across network complementarity. Breadth and total activity increase after GHO access and cross-chain expansion, while activity concentration falls in this parameterization. A single route creates substantial removal loss; dividing the same remote exposure across two routes halves the largest single-route loss.}",
            r"\label{fig:simulation-outcomes}",
            r"\begin{minipage}{0.94\textwidth}",
            r"\AVFigureSmallFont\emph{Notes:} The same 240 synthetic actors, attributes, and link draws are used in every scenario. The figure is a deterministic mechanism and sensitivity check, not an empirical calibration or treatment effect.",
            r"\end{minipage}",
            r"\end{figure*}",
            "",
        ]
    )
    return "\n".join(lines)


def svg_polyline(
    rows: list[dict[str, float]],
    metric: str,
    lower: float,
    upper: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
    scenario: str,
) -> str:
    points = " ".join(
        f"{x0 + scale(row['beta'], 0.1, 0.7, width):.2f},"
        f"{y0 + height - scale(row[metric], lower, upper, height):.2f}"
        for row in rows
    )
    return (
        f'<polyline points="{points}" fill="none" stroke="{COLORS[scenario]}" '
        f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"'
        f'{SVG_DASHES[scenario]}/>'
    )


def render_outcomes_svg(simulation: dict[str, list[dict[str, float]]]) -> str:
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="980" viewBox="0 0 1400 980" role="img" aria-labelledby="title desc">',
        '<title id="title">Four matched network-game simulation scenarios</title>',
        '<desc id="desc">Synthetic active entities, activity index, activity HHI, and route-removal loss across four beta values.</desc>',
        '<rect width="1400" height="980" fill="#FFFFFF"/>',
        '<g font-family="Latin Modern Roman, serif" fill="#17324D">',
    ]
    legend_positions = ((70, 62), (720, 62), (70, 105), (720, 105))
    for (x, y), scenario in zip(legend_positions, SCENARIOS, strict=True):
        dash = SVG_DASHES[scenario]
        pieces.append(
            f'<line x1="{x}" y1="{y}" x2="{x + 55}" y2="{y}" stroke="{COLORS[scenario]}" '
            f'stroke-width="4"{dash}/><text x="{x + 75}" y="{y + 6}" font-size="17">{html.escape(LABELS[scenario])}</text>'
        )
    origins = ((80, 180), (760, 180), (80, 560), (760, 560))
    for (title, metric, lower, upper, ticks, tick_format), (x0, y0) in zip(PANELS, origins, strict=True):
        width, height = 560.0, 250.0
        pieces.extend(
            [
                f'<text x="{x0}" y="{y0 - 24}" font-size="21" font-weight="700">{html.escape(title)}</text>',
                f'<rect x="{x0}" y="{y0}" width="{width}" height="{height}" fill="#F4F7FA" stroke="#C8D2DC"/>',
            ]
        )
        for tick in ticks:
            y = y0 + height - scale(tick, lower, upper, height)
            pieces.extend(
                [
                    f'<line x1="{x0}" y1="{y:.2f}" x2="{x0 + width}" y2="{y:.2f}" stroke="#DCE3EA"/>',
                    f'<text x="{x0 - 14}" y="{y + 5:.2f}" text-anchor="end" font-size="14" fill="#5D6B78">{tick_format.format(tick)}</text>',
                ]
            )
        for beta in (0.1, 0.3, 0.5, 0.7):
            x = x0 + scale(beta, 0.1, 0.7, width)
            pieces.append(
                f'<text x="{x:.2f}" y="{y0 + height + 26}" text-anchor="middle" font-size="14" fill="#5D6B78">{beta:.1f}</text>'
            )
        for scenario in SCENARIOS:
            pieces.append(
                svg_polyline(
                    simulation[scenario],
                    metric,
                    lower,
                    upper,
                    x0,
                    y0,
                    width,
                    height,
                    scenario,
                )
            )
        pieces.append(
            f'<text x="{x0 + width / 2}" y="{y0 + height + 55}" text-anchor="middle" font-size="15" fill="#5D6B78">β (network complementarity)</text>'
        )
    pieces.extend(
        [
            '<text x="80" y="948" font-size="16" fill="#5D6B78">Deterministic synthetic mechanism check; not empirically calibrated.</text>',
            "</g></svg>",
        ]
    )
    return "\n".join(pieces) + "\n"


def comparison_values(
    simulation: dict[str, list[dict[str, float]]],
    empirical: dict[str, dict[str, float]],
) -> dict[str, float]:
    reference = 0.5
    baseline = next(row for row in simulation["ethereum_aave"] if row["beta"] == reference)
    issuance = next(row for row in simulation["ethereum_gho"] if row["beta"] == reference)
    one_route = next(row for row in simulation["crosschain_single"] if row["beta"] == reference)
    two_routes = next(row for row in simulation["crosschain_redundant"] if row["beta"] == reference)
    return {
        "simulation_active_change": 100
        * (issuance["active_entities"] / baseline["active_entities"] - 1),
        "simulation_hhi_change": 100
        * (issuance["activity_hhi"] / baseline["activity_hhi"] - 1),
        "simulation_chain_hhi_before": issuance["chain_hhi"],
        "simulation_chain_hhi_after": one_route["chain_hhi"],
        "simulation_one_route_loss": one_route["max_route_removal_loss"],
        "simulation_two_route_loss": two_routes["max_route_removal_loss"],
        "empirical_active_change": empirical["Ethereum"]["active_percent_change"],
        "empirical_hhi_change": empirical["Ethereum"]["hhi_percent_change"],
    }


def bridge_rows(values: dict[str, float]) -> list[dict[str, str]]:
    return [
        {
            "dimension": "Activity breadth",
            "simulation": f"+{values['simulation_active_change']:.1f}% active entities",
            "empirical": f"Ethereum +{values['empirical_active_change']:.1f}% active beneficiary addresses",
            "status": "same direction",
            "style": "observed",
        },
        {
            "dimension": "Transaction concentration",
            "simulation": f"{values['simulation_hhi_change']:.1f}% activity HHI",
            "empirical": f"Ethereum {values['empirical_hhi_change']:.1f}% beneficiary HHI",
            "status": "same direction",
            "style": "observed",
        },
        {
            "dimension": "Chain dispersion",
            "simulation": (
                f"chain HHI {values['simulation_chain_hhi_before']:.3f}"
                f" → {values['simulation_chain_hhi_after']:.3f}"
            ),
            "empirical": "No comparable chain-share outcome in the locked panel",
            "status": "pending measure",
            "style": "planned",
        },
        {
            "dimension": "Route resilience",
            "simulation": (
                f"Lmax {values['simulation_one_route_loss']:.3f}"
                f" → {values['simulation_two_route_loss']:.3f}"
            ),
            "empirical": "No verified route-event table",
            "status": "blocked",
            "style": "blocked",
        },
    ]


def render_bridge_tikz(rows: list[dict[str, str]]) -> str:
    styles = {"observed": "av/observed", "planned": "av/planned", "blocked": "av/blocked"}
    status_colors = {"observed": "AVTeal", "planned": "AVViolet", "blocked": "AVSlate"}

    def tex_text(value: str) -> str:
        return (
            value.replace("%", r"\%")
            .replace("→", r"$\rightarrow$")
            .replace("Lmax", r"$L_{\max}$")
        )

    lines = [
        "% Generated by scripts/render_publication_visuals.py; do not edit by hand.",
        r"\begin{figure*}[t]",
        r"\centering",
        r"\begin{tikzpicture}[font=\AVFigureFont]",
        r"\node[av/tag,fill=AVNavy] at (7.55,5.35) {DIRECTIONAL BRIDGE \textbullet{} NOT A CALIBRATION OR CAUSAL TEST};",
        r"\node[font=\bfseries,text width=28mm,align=left] at (1.35,4.55) {Dimension};",
        r"\node[font=\bfseries,text=AVBlue,text width=40mm,align=center] at (5.00,4.55) {Synthetic mechanism\\($\beta=0.50$)};",
        r"\node[font=\bfseries,text=AVTeal,text width=44mm,align=center] at (9.70,4.55) {Audited empirical\\evidence};",
        r"\node[font=\bfseries,text width=28mm,align=center] at (13.65,4.55) {Interpretation};",
    ]
    y_values = (3.55, 2.35, 1.15, -0.05)
    for row, y in zip(rows, y_values, strict=True):
        style = styles[row["style"]]
        color = status_colors[row["style"]]
        dimension = {
            "Activity breadth": r"Activity\\breadth",
            "Transaction concentration": r"Transaction\\concentration",
            "Chain dispersion": r"Chain\\dispersion",
            "Route resilience": r"Route\\resilience",
        }[row["dimension"]]
        simulation = tex_text(row["simulation"])
        empirical = tex_text(row["empirical"])
        empirical = empirical.replace(" active beneficiary", r"\\active beneficiary")
        status = tex_text(row["status"].upper())
        lines.extend(
            [
                rf"\node[anchor=west,font=\bfseries,text width=27mm] at (0,{y:.2f}) {{{dimension}}};",
                rf"\node[av/model,text width=36mm,minimum height=10mm] (s{len(lines)}) at (5.00,{y:.2f}) {{{simulation}}};",
                rf"\node[{style},text width=42mm,minimum height=10mm] (e{len(lines)}) at (9.70,{y:.2f}) {{{empirical}}};",
                rf"\node[av/tag,fill={color},text width=22mm,align=center] at (13.65,{y:.2f}) {{{status}}};",
            ]
        )
    lines.extend(
        [
            r"\node[av/card,draw=AVRule,fill=AVLight,text width=145mm,minimum height=10mm] at (7.55,-1.15) {Comparable signs in the first two rows support the model's qualitative mechanism, but different units and the absence of a counterfactual prevent calibration or causal interpretation.};",
            r"\end{tikzpicture}",
            r"\caption{Simulation-to-evidence bridge. At the reference parameter, the synthetic issuance comparison and the audited Ethereum pre/post address-level comparison move in the same direction for breadth and concentration. Chain-dispersion and route-resilience counterparts remain unmeasured or blocked.}",
            r"\label{fig:simulation-empirical-bridge}",
            r"\end{figure*}",
            "",
        ]
    )
    return "\n".join(lines)


def render_bridge_svg(rows: list[dict[str, str]]) -> str:
    stroke = {"observed": "#147D78", "planned": "#6857C7", "blocked": "#5D6B78"}
    fill = {"observed": "#EFF9F7", "planned": "#F4F1FC", "blocked": "#F1F3F5"}
    pieces = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="650" viewBox="0 0 1500 650" role="img" aria-labelledby="title desc">',
        '<title id="title">Simulation-to-empirical evidence bridge</title>',
        '<desc id="desc">Directional comparison of synthetic and audited descriptive evidence, with pending and blocked outcomes identified.</desc>',
        '<rect width="1500" height="650" fill="#FFFFFF"/>',
        '<g font-family="Latin Modern Roman, serif" fill="#17324D">',
        '<rect x="450" y="24" width="600" height="38" rx="9" fill="#17324D"/>',
        '<text x="750" y="49" text-anchor="middle" font-size="17" font-weight="700" fill="#FFFFFF">DIRECTIONAL BRIDGE · NOT A CALIBRATION OR CAUSAL TEST</text>',
        '<text x="55" y="105" font-size="19" font-weight="700">Dimension</text>',
        '<text x="315" y="105" font-size="19" font-weight="700" fill="#2F6B9A">Synthetic mechanism (β = 0.50)</text>',
        '<text x="760" y="105" font-size="19" font-weight="700" fill="#147D78">Audited empirical evidence</text>',
        '<text x="1260" y="105" font-size="19" font-weight="700">Interpretation</text>',
    ]
    for index, row in enumerate(rows):
        y = 135 + index * 105
        pieces.extend(
            [
                f'<text x="55" y="{y + 36}" font-size="18" font-weight="700">{html.escape(row["dimension"])}</text>',
                f'<rect x="300" y="{y}" width="390" height="72" rx="12" fill="#F1F6FA" stroke="#2F6B9A" stroke-width="2"/>',
                f'<text x="495" y="{y + 43}" text-anchor="middle" font-size="17">{html.escape(row["simulation"])}</text>',
                f'<rect x="740" y="{y}" width="440" height="72" rx="12" fill="{fill[row["style"]]}" stroke="{stroke[row["style"]]}" stroke-width="2"'
                + (' stroke-dasharray="9 6"' if row["style"] != "observed" else "")
                + "/>",
                f'<text x="960" y="{y + 34}" text-anchor="middle" font-size="16">{html.escape(row["empirical"])}</text>',
                f'<rect x="1245" y="{y + 13}" width="205" height="45" rx="10" fill="{stroke[row["style"]]}"/>',
                f'<text x="1347" y="{y + 42}" text-anchor="middle" font-size="15" font-weight="700" fill="#FFFFFF">{html.escape(row["status"].upper())}</text>',
            ]
        )
    pieces.extend(
        [
            '<rect x="55" y="565" width="1390" height="58" rx="12" fill="#F4F7FA" stroke="#C8D2DC"/>',
            '<text x="750" y="589" text-anchor="middle" font-size="15">Comparable signs in the first two rows support a qualitative mechanism only.</text>',
            '<text x="750" y="611" text-anchor="middle" font-size="15">Different units and no counterfactual prevent calibration or causal interpretation.</text>',
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
    ) -> None:
        cell_id = self._id()
        safe = html.escape(value).replace("\n", "&#xa;")
        self.cells.append(
            f'<mxCell id="{cell_id}" value="{safe}" style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" as="geometry"/>'
            "</mxCell>"
        )

    def edge(self, x1: float, y1: float, x2: float, y2: float, style: str) -> None:
        cell_id = self._id()
        self.cells.append(
            f'<mxCell id="{cell_id}" style="{style}" edge="1" parent="1">'
            '<mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x1:.1f}" y="{y1:.1f}" as="sourcePoint"/>'
            f'<mxPoint x="{x2:.1f}" y="{y2:.1f}" as="targetPoint"/>'
            "</mxGeometry></mxCell>"
        )


def mxfile(name: str, width: int, height: int, cells: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxfile host="app.diagrams.net" agent="aave-bns" version="24.7.17">'
        f'<diagram id="{html.escape(name)}" name="{html.escape(name)}">'
        f'<mxGraphModel dx="{width}" dy="{height}" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{width}" pageHeight="{height}" math="1" shadow="0">'
        '<root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        + "".join(cells)
        + "</root></mxGraphModel></diagram></mxfile>\n"
    )


def _drawio_polyline(
    builder: DrawioBuilder,
    points: list[tuple[float, float]],
    *,
    color: str,
    dash: str = "",
    width: float = 3.0,
) -> None:
    """Emit a data line as individually editable draw.io segments."""
    style = (
        "endArrow=none;html=0;rounded=0;"
        f"strokeColor={color};strokeWidth={width:.1f};{dash}"
    )
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:], strict=True):
        builder.edge(x1, y1, x2, y2, style)


def render_outcomes_drawio(
    simulation: dict[str, list[dict[str, float]]],
) -> str:
    """Render the four-panel simulation chart with native editable cells."""
    b = DrawioBuilder()
    text = (
        "whiteSpace=wrap;html=0;align=left;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontSize=16;fontColor=#17324D;"
        "strokeColor=none;fillColor=none;"
    )
    centered = text.replace("align=left", "align=center")
    panel = "rounded=0;html=0;strokeColor=#C8D2DC;fillColor=#F4F7FA;strokeWidth=1;"
    grid = "endArrow=none;html=0;strokeColor=#DCE3EA;strokeWidth=1;"

    legend_positions = ((70, 62), (720, 62), (70, 105), (720, 105))
    for (x, y), scenario in zip(legend_positions, SCENARIOS, strict=True):
        b.edge(
            x,
            y,
            x + 55,
            y,
            "endArrow=none;html=0;"
            f"strokeColor={COLORS[scenario]};strokeWidth=4;{DRAWIO_DASHES[scenario]}",
        )
        b.vertex(LABELS[scenario], x + 75, y - 20, 250, 40, text)

    origins = ((80, 180), (760, 180), (80, 560), (760, 560))
    for (title, metric, lower, upper, ticks, tick_format), (x0, y0) in zip(
        PANELS, origins, strict=True
    ):
        plot_width, plot_height = 560.0, 250.0
        b.vertex(title, x0, y0 - 43, plot_width, 36, text + "fontSize=19;fontStyle=1;")
        b.vertex("", x0, y0, plot_width, plot_height, panel)
        for tick in ticks:
            y = y0 + plot_height - scale(tick, lower, upper, plot_height)
            b.edge(x0, y, x0 + plot_width, y, grid)
            b.vertex(
                tick_format.format(tick),
                x0 - 78,
                y - 14,
                65,
                28,
                text + "align=right;fontSize=13;fontColor=#5D6B78;",
            )
        for beta in (0.1, 0.3, 0.5, 0.7):
            x = x0 + scale(beta, 0.1, 0.7, plot_width)
            b.vertex(
                f"{beta:.1f}",
                x - 25,
                y0 + plot_height + 6,
                50,
                26,
                centered + "fontSize=13;fontColor=#5D6B78;",
            )
        for scenario in SCENARIOS:
            points = [
                (
                    x0 + scale(row["beta"], 0.1, 0.7, plot_width),
                    y0
                    + plot_height
                    - scale(row[metric], lower, upper, plot_height),
                )
                for row in simulation[scenario]
            ]
            _drawio_polyline(
                b,
                points,
                color=COLORS[scenario],
                dash=DRAWIO_DASHES[scenario],
                width=4.0,
            )
        b.vertex(
            "β (network complementarity)",
            x0 + 120,
            y0 + plot_height + 34,
            320,
            30,
            centered + "fontSize=14;fontColor=#5D6B78;",
        )
    b.vertex(
        "Deterministic synthetic mechanism check; not empirically calibrated.",
        80,
        940,
        1240,
        30,
        text + "fontSize=15;fontColor=#5D6B78;",
    )
    return mxfile("Four scenario simulation outcomes", 1400, 980, b.cells)


def render_real_v3_drawio() -> str:
    """Render the real-v3 measurement chart as native editable draw.io cells."""
    weekly_path = ROOT / "outputs/real_v3/ethereum/weekly_entity_sensitivity.csv"
    weekly = sorted(read_csv(weekly_path), key=lambda row: int(row["event_week"]))
    if [int(row["event_week"]) for row in weekly] != list(range(-16, 17)):
        raise ValueError("real-v3 draw.io source requires the complete -16..+16 panel")

    b = DrawioBuilder()
    text = (
        "whiteSpace=wrap;html=0;align=left;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontSize=15;fontColor=#17324D;"
        "strokeColor=none;fillColor=none;"
    )
    centered = text.replace("align=left", "align=center")
    panel = "rounded=1;arcSize=4;html=0;strokeColor=#C8D2DC;fillColor=#F4F7FA;strokeWidth=1;"
    grid = "endArrow=none;html=0;strokeColor=#DCE3EA;strokeWidth=1;"
    left, plot_width = 105.0, 1025.0
    top_one, top_two, panel_height = 100.0, 420.0, 215.0
    effective_max = math.ceil(
        max(
            max(float(row["effective_active_addresses"]) for row in weekly),
            max(
                float(row["effective_curated_entities_sensitivity"])
                for row in weekly
            ),
        )
        / 20
    ) * 20
    share_max = 0.60
    treatment_x = left + 16 / 32 * plot_width

    b.vertex(
        "Address breadth and infrastructure dependence across event time",
        150,
        14,
        900,
        34,
        centered + "fontSize=24;fontStyle=1;",
    )
    b.vertex(
        "Descriptive Ethereum Aave V3 measurements; event week 0 is GHO activation, not a causal estimate",
        150,
        50,
        900,
        28,
        centered + "fontSize=14;fontColor=#5D6B78;",
    )
    for title, top, maximum, percent in (
        ("A. Effective activity breadth", top_one, float(effective_max), False),
        ("B. Infrastructure incidence shares", top_two, share_max, True),
    ):
        b.vertex(title, left, top - 32, 430, 28, text + "fontSize=17;fontStyle=1;")
        b.vertex("", left, top, plot_width, panel_height, panel)
        b.edge(
            treatment_x,
            top,
            treatment_x,
            top + panel_height,
            "endArrow=none;html=0;dashed=1;dashPattern=7 6;"
            "strokeColor=#6857C7;strokeWidth=2;",
        )
        for fraction in (0.0, 0.5, 1.0):
            y = top + panel_height - fraction * panel_height
            b.edge(left, y, left + plot_width, y, grid)
            label = f"{fraction * maximum:.0%}" if percent else f"{fraction * maximum:.0f}"
            b.vertex(
                label,
                left - 78,
                y - 14,
                62,
                28,
                text + "align=right;fontSize=13;fontColor=#5D6B78;",
            )

    series = (
        (
            "effective_active_addresses",
            top_one,
            float(effective_max),
            "#2F6B9A",
            "",
        ),
        (
            "effective_curated_entities_sensitivity",
            top_one,
            float(effective_max),
            "#6857C7",
            "dashed=1;dashPattern=8 4;",
        ),
        ("contract_incidence_share", top_two, share_max, "#147D78", ""),
        (
            "protocol_infrastructure_incidence_share",
            top_two,
            share_max,
            "#C46B1A",
            "dashed=1;dashPattern=10 4 2 4;",
        ),
    )
    for column, top, maximum, color, dash in series:
        points = [
            (
                left + (int(row["event_week"]) + 16) / 32 * plot_width,
                top
                + panel_height
                - min(max(float(row[column]) / maximum, 0.0), 1.0) * panel_height,
            )
            for row in weekly
        ]
        _drawio_polyline(b, points, color=color, dash=dash, width=4.0)

    for week in (-16, -8, 0, 8, 16):
        x = left + (week + 16) / 32 * plot_width
        b.vertex(
            f"{week:+d}",
            x - 28,
            646,
            56,
            25,
            centered + "fontSize=13;fontColor=#5D6B78;",
        )
    b.vertex(
        "Event week relative to governance-controlled GHO activation",
        350,
        680,
        500,
        28,
        centered + "fontSize=14;",
    )
    legend_items = (
        (720, 88, "effective addresses", "#2F6B9A", ""),
        (905, 88, "curated-entity sensitivity", "#6857C7", "dashed=1;dashPattern=8 4;"),
        (750, 408, "all contract addresses", "#147D78", ""),
        (950, 408, "curated Aave infrastructure", "#C46B1A", "dashed=1;dashPattern=10 4 2 4;"),
    )
    for x, y, label, color, dash in legend_items:
        b.edge(
            x,
            y,
            x + 35,
            y,
            f"endArrow=none;html=0;strokeColor={color};strokeWidth=4;{dash}",
        )
        b.vertex(label, x + 44, y - 15, 205, 30, text + "fontSize=13;")
    return mxfile("Real v3 measurement", 1200, 720, b.cells)


def drawio_flow(name: str, cards: list[tuple[str, str, str]], note: str) -> str:
    b = DrawioBuilder()
    card_style = (
        "rounded=1;whiteSpace=wrap;html=0;align=center;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontSize=18;strokeWidth=2;"
    )
    edge_style = "endArrow=block;html=0;strokeColor=#17324D;strokeWidth=2;"
    x_positions = (70, 400, 730, 1060)
    for x, (label, stroke, fill) in zip(x_positions, cards, strict=True):
        b.vertex(label, x, 100, 280, 170, card_style + f"strokeColor={stroke};fillColor={fill};")
    for x1, x2 in zip(x_positions[:-1], x_positions[1:], strict=True):
        b.edge(x1 + 280, 185, x2, 185, edge_style)
    b.vertex(
        note,
        70,
        330,
        1270,
        95,
        card_style + "strokeColor=#C8D2DC;fillColor=#F4F7FA;fontSize=17;",
    )
    return mxfile(name, 1410, 500, b.cells)


def render_concept_drawios() -> dict[Path, str]:
    # Figure 1 is a real teaser rather than a row of text-only boxes. All icons
    # use native draw.io geometry so the editable source contains no bitmap.
    b = DrawioBuilder()
    card = (
        "rounded=1;arcSize=10;whiteSpace=wrap;html=0;align=center;"
        "verticalAlign=middle;fontFamily=Latin Modern Roman;fontSize=17;strokeWidth=2;"
    )
    text_only = (
        "whiteSpace=wrap;html=0;align=center;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontSize=17;strokeColor=none;fillColor=none;"
    )
    tag = (
        "rounded=1;arcSize=18;whiteSpace=wrap;html=0;align=center;"
        "verticalAlign=middle;fontFamily=Latin Modern Roman;fontSize=14;fontStyle=1;"
        "fontColor=#FFFFFF;strokeColor=none;"
    )
    flow = "endArrow=block;html=0;strokeWidth=2;strokeColor=#17324D;"
    no_arrow = "endArrow=none;html=0;strokeWidth=2;strokeColor=#2F6B9A;"
    xs = (45, 385, 725, 1065)
    for left, right in zip(xs[:-1], xs[1:], strict=True):
        b.edge(left + 300, 205, right, 205, flow)
    card_specs = (
        (xs[0], "#147D78", "#EFF9F7"),
        (xs[1], "#2F6B9A", "#F1F6FA"),
        (xs[2], "#147D78", "#EFF9F7"),
        (xs[3], "#6857C7", "#F4F1FC"),
    )
    for x, stroke, fill in card_specs:
        b.vertex("", x, 35, 300, 340, card + f"strokeColor={stroke};fillColor={fill};")

    # Protocol-shock icon: stablecoin plus two chain blocks.
    b.vertex("G", 160, 65, 70, 70, "ellipse;html=0;fontFamily=Latin Modern Roman;fontSize=24;fontStyle=1;fontColor=#147D78;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.vertex("", 112, 87, 34, 30, "shape=hexagon;html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.vertex("", 244, 87, 34, 30, "shape=hexagon;html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.edge(146, 102, 160, 102, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(230, 102, 244, 102, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")

    # Network icon: five editable nodes and six editable edges.
    network_points = ((500, 83), (535, 60), (572, 83), (515, 126), (558, 126))
    for x1, y1, x2, y2 in ((500, 83, 535, 60), (535, 60, 572, 83), (500, 83, 515, 126),
                           (515, 126, 558, 126), (558, 126, 572, 83), (535, 60, 558, 126)):
        b.edge(x1, y1, x2, y2, no_arrow)
    for x, y in network_points:
        b.vertex("", x - 7, y - 7, 14, 14, "ellipse;html=0;strokeColor=#2F6B9A;fillColor=#2F6B9A;")

    # Governed-data icon: versioned store linked to two reproducible blocks.
    b.vertex("", 835, 62, 74, 78, "shape=cylinder3;boundedLbl=1;html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.vertex("", 930, 67, 32, 32, "html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.vertex("", 945, 113, 32, 32, "html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.edge(909, 98, 930, 83, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(946, 99, 961, 113, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")

    # Evidence icon: axes, treatment gate, line, and live data marks.
    b.edge(1162, 132, 1162, 65, "endArrow=none;strokeColor=#6857C7;strokeWidth=2;")
    b.edge(1162, 65, 1272, 65, "endArrow=none;strokeColor=#6857C7;strokeWidth=2;")
    b.edge(1215, 65, 1215, 132, "endArrow=none;dashed=1;strokeColor=#6857C7;strokeWidth=2;")
    for x1, y1, x2, y2 in ((1168, 121, 1195, 108), (1195, 108, 1222, 102), (1222, 102, 1260, 76)):
        b.edge(x1, y1, x2, y2, "endArrow=none;strokeColor=#6857C7;strokeWidth=3;")
    for x, y in ((1168, 121), (1195, 108), (1222, 102), (1260, 76)):
        b.vertex("", x - 5, y - 5, 10, 10, "ellipse;html=0;strokeColor=#6857C7;fillColor=#6857C7;")

    body_specs = (
        (xs[0], "Protocol shocks\nGHO issuance (2023)\nArbitrum reserve activation (2024)"),
        (xs[1], "Network mechanisms\nLiquidity and adoption benefits versus concentration and route costs"),
        (xs[2], "Governed data\n33-week panels, address roles, treatment clocks, and manifests"),
        (xs[3], "Evidence status\nSimulation and audited descriptives; two-chain DiD is diagnostic only"),
    )
    for x, label in body_specs:
        b.vertex(label, x + 20, 148, 260, 145, text_only + "fontStyle=0;")
    for x, label, fill in (
        (xs[0], "VERIFIED", "#147D78"),
        (xs[1], "SYNTHETIC", "#2F6B9A"),
        (xs[2], "GOVERNED", "#147D78"),
        (xs[3], "DIAGNOSTIC", "#6857C7"),
    ):
        b.vertex(label, x + 85, 322, 130, 32, tag + f"fillColor={fill};")

    b.vertex("Ethereum observed\nBreadth +91.0%; beneficiary HHI −31.5%", 55, 410, 400, 86,
             card + "strokeColor=#147D78;fillColor=#EFF9F7;fontSize=15;")
    b.vertex("Arbitrum observed\nBreadth +1.7%; beneficiary HHI +58.1%", 505, 410, 400, 86,
             card + "strokeColor=#C46B1A;fillColor=#FCF5EC;fontSize=15;")
    b.vertex("Claim boundary\nActor direction and causal effect are not identified", 955, 410, 400, 86,
             card + "dashed=1;strokeColor=#5D6B78;fillColor=#F1F3F5;fontSize=15;")
    b.vertex("Teaser conclusion: broader protocol use and effective decentralization are not the same outcome.",
             55, 535, 1300, 70,
             card + "strokeColor=#17324D;fillColor=#F4F7FA;fontSize=17;fontStyle=1;")
    fig01 = mxfile("Figure 1 Graphical teaser", 1410, 660, b.cells)
    fig03 = drawio_flow(
        "Figure 3 Simulation design",
        [
            ("S0 Benchmark\nEthereum, pre-GHO\n0 routes", "#2F6B9A", "#F1F6FA"),
            ("S1 Issuance\nEthereum + GHO\nnative access", "#6857C7", "#F4F1FC"),
            ("S2 Expansion\nCross-chain GHO\n1 route", "#C46B1A", "#FCF5EC"),
            ("S3 Redundancy\nCross-chain GHO\n2 routes", "#147D78", "#EFF9F7"),
        ],
        "Illustrative mechanism: the same 240 synthetic actors, attributes, and link draws are held fixed across scenarios.",
    )

    b = DrawioBuilder()
    axis = "endArrow=block;html=0;strokeColor=#17324D;strokeWidth=2;"
    timeline_card = (
        "rounded=1;arcSize=10;whiteSpace=wrap;html=0;align=center;"
        "verticalAlign=middle;fontFamily=Latin Modern Roman;fontSize=16;strokeWidth=2;"
    )
    b.edge(70, 300, 1340, 300, axis)
    timeline = (
        (75, 10, "Ethereum benchmark\nAAVE migration\nOctober 2020\nOBSERVED", "#147D78", "#EFF9F7", False),
        (405, 340, "On-chain GHO activation\n15 July 2023\nblock 17,699,249\nLOCKED A+", "#147D78", "#EFF9F7", False),
        (735, 10, "Arbitrum GHO activation\n2 July 2024\nblock 228,027,379\nLOCKED A+", "#147D78", "#EFF9F7", False),
        (1065, 340, "Additional cohorts\nComparable weekly panels\nSUPPORT-GATED", "#6857C7", "#F4F1FC", True),
    )
    for x, y, label, stroke, fill, dashed in timeline:
        connector_y = y + 270 if y < 300 else y
        b.edge(x + 125, connector_y, x + 125, 300,
               f"endArrow=none;dashed=1;strokeColor={stroke};strokeWidth=2;")
        b.vertex("", x, y, 250, 270,
                 timeline_card + ("dashed=1;" if dashed else "") + f"strokeColor={stroke};fillColor={fill};")
        b.vertex(label, x + 15, y + 76, 220, 174,
                 "whiteSpace=wrap;html=0;align=center;verticalAlign=middle;fontFamily=Latin Modern Roman;fontSize=15;fontStyle=0;strokeColor=none;fillColor=none;")
    # Native timeline icons.
    for x, y in ((145, 70), (180, 55), (215, 70)):
        b.vertex("", x, y, 28, 28, "html=0;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.edge(173, 84, 180, 69, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(208, 69, 215, 84, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.vertex("G", 495, 360, 70, 70, "ellipse;html=0;fontFamily=Latin Modern Roman;fontSize=22;fontStyle=1;fontColor=#147D78;strokeColor=#147D78;fillColor=#FFFFFF;strokeWidth=2;")
    b.edge(470, 395, 495, 395, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(565, 395, 590, 395, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    # Bridge icon.
    b.edge(800, 112, 920, 112, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(812, 65, 812, 112, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(908, 65, 908, 112, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(812, 70, 840, 88, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(840, 88, 880, 88, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    b.edge(880, 88, 908, 70, "endArrow=none;strokeColor=#147D78;strokeWidth=2;")
    # Multichain icon.
    for x, y in ((1132, 365), (1190, 405), (1248, 365)):
        b.vertex("", x, y, 34, 30, "shape=hexagon;html=0;strokeColor=#6857C7;fillColor=#FFFFFF;strokeWidth=2;")
    b.edge(1166, 380, 1190, 420, "endArrow=none;strokeColor=#6857C7;strokeWidth=2;")
    b.edge(1224, 420, 1248, 380, "endArrow=none;strokeColor=#6857C7;strokeWidth=2;")
    for x, label, y in ((200, "2020", 310), (530, "2023", 266), (860, "2024", 310), (1190, "later", 266)):
        b.vertex(label, x - 45, y, 90, 30, "whiteSpace=wrap;html=0;align=center;fontFamily=Latin Modern Roman;fontSize=18;fontStyle=1;fontColor=#17324D;strokeColor=none;fillColor=none;")
    b.vertex(
        "Clock rule: proposal, technical readiness, first indexed use, and public changelog are separate records. Aave changelog records GHO on 16 July 2023; it is not substituted for the Ethereum on-chain treatment clock.",
        90,
        650,
        1230,
        92,
        timeline_card + "strokeColor=#C8D2DC;fillColor=#F4F7FA;fontSize=15;",
    )
    fig02 = mxfile("Figure 2 Institutional timeline", 1410, 790, b.cells)

    style = (
        "rounded=1;whiteSpace=wrap;html=0;align=center;verticalAlign=middle;"
        "fontFamily=Latin Modern Roman;fontSize=18;strokeWidth=2;"
    )
    b = DrawioBuilder()
    panels = [
        (60, "Observed address proxy\n118,806 events\nHHI −37.0%", "#147D78", "#EFF9F7"),
        (515, "Actor identified set\nChange [−0.996445, 0.994353]\nDirection not identified", "#6857C7", "#F4F1FC"),
        (970, "Claim gate\n0 actor must-links\nNo causal estimate", "#5D6B78", "#F1F3F5"),
    ]
    for x, label, stroke, fill in panels:
        b.vertex(label, x, 85, 380, 220, style + f"strokeColor={stroke};fillColor={fill};")
    b.edge(440, 195, 515, 195, axis)
    b.edge(895, 195, 970, 195, axis)
    b.vertex(
        "Identified sets are descriptive bounds, not confidence intervals or treatment effects.",
        170,
        370,
        1070,
        80,
        style + "strokeColor=#C8D2DC;fillColor=#F4F7FA;",
    )
    fig04 = mxfile("Figure 7 Partial identification", 1410, 520, b.cells)
    return {
        ASSET_DIR / "fig01_research_design.drawio": fig01,
        ASSET_DIR / "fig02_institutional_timeline.drawio": fig02,
        ASSET_DIR / "fig03_simulation_design.drawio": fig03,
        ASSET_DIR / "fig04_real_v4_partial_identification.drawio": fig04,
    }


def render_bridge_drawio(rows: list[dict[str, str]]) -> str:
    b = DrawioBuilder()
    card = "rounded=1;whiteSpace=wrap;html=0;align=center;fontFamily=Latin Modern Roman;fontSize=16;strokeWidth=2;"
    b.vertex(
        "DIRECTIONAL BRIDGE · NOT A CALIBRATION OR CAUSAL TEST",
        360,
        20,
        720,
        50,
        card + "strokeColor=#17324D;fillColor=#17324D;fontColor=#FFFFFF;fontStyle=1;",
    )
    for index, row in enumerate(rows):
        y = 110 + index * 115
        b.vertex(row["dimension"], 40, y, 240, 75, card + "strokeColor=#C8D2DC;fillColor=#FFFFFF;fontStyle=1;")
        b.vertex(row["simulation"], 315, y, 360, 75, card + "strokeColor=#2F6B9A;fillColor=#F1F6FA;")
        evidence_stroke = {"observed": "#147D78", "planned": "#6857C7", "blocked": "#5D6B78"}[row["style"]]
        evidence_fill = {"observed": "#EFF9F7", "planned": "#F4F1FC", "blocked": "#F1F3F5"}[row["style"]]
        dashed = "dashed=1;" if row["style"] != "observed" else ""
        b.vertex(row["empirical"], 710, y, 450, 75, card + dashed + f"strokeColor={evidence_stroke};fillColor={evidence_fill};")
        b.vertex(row["status"].upper(), 1200, y + 12, 210, 50, card + f"strokeColor={evidence_stroke};fillColor={evidence_stroke};fontColor=#FFFFFF;fontStyle=1;")
    b.vertex(
        "Comparable signs support a qualitative mechanism only; different units and no counterfactual prevent causal interpretation.",
        160,
        585,
        1180,
        60,
        card + "strokeColor=#C8D2DC;fillColor=#F4F7FA;",
    )
    return mxfile("Simulation empirical bridge", 1460, 680, b.cells)


def write_outputs() -> None:
    simulation = load_simulation()
    empirical = empirical_pre_post()
    values = comparison_values(simulation, empirical)
    rows = bridge_rows(values)

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    outputs: dict[Path, str] = {
        OUTCOMES_TEX: render_outcomes_tikz(simulation),
        BRIDGE_TEX: render_bridge_tikz(rows),
        OUTCOMES_SVG: render_outcomes_svg(simulation),
        BRIDGE_SVG: render_bridge_svg(rows),
    }
    outputs[OUTCOMES_DRAWIO] = render_outcomes_drawio(simulation)
    outputs[BRIDGE_DRAWIO] = render_bridge_drawio(rows)
    # Figure 1 is a paired editorial TikZ/draw.io master rather than a
    # data-generated asset; refreshing simulation outputs must not replace it.
    outputs.update(
        {
            path: body
            for path, body in render_concept_drawios().items()
            if path.name != "fig01_research_design.drawio"
        }
    )

    real_v3_svg = ROOT / "docs/figures/real_v3_measurement.svg"
    if real_v3_svg.exists():
        outputs[ASSET_DIR / "fig03_real_v3_measurement.drawio"] = (
            render_real_v3_drawio()
        )
    # Figure 4 uses the fully editable card-based draw.io source emitted above;
    # its SVG remains a separate audited export rather than an embedded duplicate.

    for path, body in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        if path.suffix == ".drawio":
            ET.parse(path)

    manifest = {
        "schema_version": 1,
        "evidence_status": {
            "simulation": "deterministic_synthetic_not_calibrated",
            "empirical": "audited_address_level_descriptive_noncausal",
            "comparison": "directional_bridge_not_a_causal_test",
        },
        "inputs": {
            str(SIMULATION_PATH.relative_to(ROOT)): sha256(SIMULATION_PATH),
            str(EMPIRICAL_PATH.relative_to(ROOT)): sha256(EMPIRICAL_PATH),
        },
        "outputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in sorted(outputs)
        },
        "reference_beta": 0.5,
        "comparison_values": values,
        "claim_gates": {
            "empirically_calibrated_simulation": False,
            "causal_estimate_produced": False,
            "route_evidence_produced": False,
            "entity_level_primary_result_produced": False,
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTCOMES_TEX)
    print(BRIDGE_TEX)
    print(OUTCOMES_SVG)
    print(BRIDGE_SVG)
    print(MANIFEST_PATH)


if __name__ == "__main__":
    write_outputs()
