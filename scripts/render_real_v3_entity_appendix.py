# Long SVG and TeX literals remain visually auditable when kept as complete output lines.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd


def load_audit(root: Path) -> dict[str, object]:
    output = root / "outputs/real_v3/ethereum"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    weekly = pd.read_csv(output / "weekly_entity_sensitivity.csv")
    weekly_action_path = output / "weekly_action_entity_sensitivity.csv"
    weekly_action = (
        pd.read_csv(weekly_action_path) if weekly_action_path.is_file() else None
    )
    address_types = pd.read_csv(output / "address_type_summary.csv")
    checks = pd.read_csv(output / "cross_provider_code_checks.csv")
    labels = pd.read_csv(
        root / "data/metadata/real_v3_ethereum_curated_labels.csv",
        keep_default_na=False,
    )
    if int(summary["address_count"]) != 15_762:
        raise ValueError("real_v3 address count drifted")
    if int(summary["event_incidence_count"]) != 148_437:
        raise ValueError("real_v3 event-incidence count drifted")
    if len(weekly) != 33 or (weekly_action is not None and len(weekly_action) != 165):
        raise ValueError("real_v3 weekly panels are not complete Cartesian panels")
    if len(checks) != 32 or not checks["exact_match"].astype(bool).all():
        raise ValueError("real_v3 independent-provider checks are incomplete or failed")
    if bool(summary["entity_gate"]["passed"]):
        raise ValueError("The v0.1.0 entity gate is expected to fail closed")
    if bool(summary["entity_level_primary_result_produced"]):
        raise ValueError("real_v3 must not claim a primary entity-level result")
    if bool(summary["causal_estimate_produced"]):
        raise ValueError("real_v3 must not claim a causal estimate")
    return {
        "summary": summary,
        "weekly": weekly,
        "address_types": address_types,
        "checks": checks,
        "labels": labels,
    }


def period_means(weekly: pd.DataFrame) -> list[dict[str, float | str]]:
    periods = [
        ("Pre ($-16$ to $-1$)", weekly[weekly["event_week"] < 0]),
        ("Week 0", weekly[weekly["event_week"] == 0]),
        ("Post ($+1$ to $+16$)", weekly[weekly["event_week"] > 0]),
    ]
    rows = []
    for label, frame in periods:
        rows.append(
            {
                "period": label,
                "effective_addresses": float(frame["effective_active_addresses"].mean()),
                "effective_curated": float(
                    frame["effective_curated_entities_sensitivity"].mean()
                ),
                "contract_share": float(frame["contract_incidence_share"].mean()),
                "protocol_share": float(
                    frame["protocol_infrastructure_incidence_share"].mean()
                ),
            }
        )
    return rows


def render_markdown(audit: dict[str, object]) -> str:
    summary = audit["summary"]
    weekly = audit["weekly"]
    labels = audit["labels"]
    periods = period_means(weekly)
    contract_address_share = summary["smart_contract_address_count"] / summary["address_count"]
    lines = [
        "# Audited Ethereum `real_v3` contract-role and entity layer",
        "",
        "## Status",
        "",
        "`real_v3-ethereum-v0.1.0` converts the 15,762-address `real_v2` participant",
        "universe into a versioned contract-role and entity-annotation release. Historical",
        "runtime code is checked at every address's first and last observed blocks. Ten",
        "protocol or asset contracts are mapped from pinned 2023 Aave address-book snapshots.",
        "All other addresses remain separate and unresolved.",
        "",
        "This is a descriptive measurement layer. The economic-actor coverage gate fails,",
        "so the release produces no primary entity-level result and no causal estimate.",
        "",
        "![Address, entity-sensitivity, and infrastructure measurements across event time](figures/real_v3_measurement.svg)",
        "",
        "## Main audit results",
        "",
        "| Item | Audited value |",
        "|---|---:|",
        f"| Participant addresses | {summary['address_count']:,} |",
        (
            f"| Addresses with runtime code observed | {summary['smart_contract_address_count']:,} "
            f"({contract_address_share:.2%}) |"
        ),
        f"| Addresses with no runtime code at either boundary | {summary['code_absent_address_count']:,} |",
        f"| Deduplicated event-address incidences | {summary['event_incidence_count']:,} |",
        (
            f"| Incidences carried by contract addresses | "
            f"{summary['contract_event_incidence_count']:,} "
            f"({summary['contract_event_incidence_share']:.2%}) |"
        ),
        f"| Primary-source curated labels | {summary['curated_label_address_count']:,} addresses |",
        (
            f"| Incidences covered by curated labels | "
            f"{summary['curated_label_event_incidence_count']:,} "
            f"({summary['curated_label_event_incidence_coverage']:.2%}) |"
        ),
        f"| Runtime-code infrastructure families | {summary['infrastructure_family_count']:,} |",
        f"| Independent-provider bytecode checks | {summary['validation_check_count']:,}/{summary['validation_check_count']:,} exact |",
        "| Economic-actor incidence coverage | 0.00% |",
        "| Entity gate | **Failed closed** |",
        "| Causal estimate | **Not produced** |",
        "",
        "The contrast is substantive: contract addresses are only about 7.2% of observed",
        "addresses but carry 38.1% of event-address incidences. The ten official labels are",
        "less than 0.1% of addresses yet cover 21.9% of incidences, largely because Aave",
        "gateways and adapters mediate many actions. Counting every contract address as an",
        "independent user therefore overstates economic participation breadth.",
        "",
        "## Descriptive event-time comparison",
        "",
        "| Period | Effective addresses | Curated-entity sensitivity | Contract incidence share | Aave infrastructure share |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in periods:
        lines.append(
            f"| {row['period'].replace('$', '')} | {row['effective_addresses']:.2f} | "
            f"{row['effective_curated']:.2f} | {row['contract_share']:.2%} | "
            f"{row['protocol_share']:.2%} |"
        )
    lines.extend(
        [
            "",
            "These are unadjusted period means, not treatment effects. They do not control",
            "for reserve composition, market growth, common shocks, anticipation, or selection.",
            "",
            "## Curated primary-source labels",
            "",
            "| Address label | Address | Scope | Confidence | Pinned source |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in labels.sort_values("address_label").itertuples(index=False):
        lines.append(
            f"| {row.address_label} | `{row.address}` | {row.entity_scope} | "
            f"{float(row.confidence):.2f} | [address book]({row.source_url}) |"
        )
    lines.extend(
        [
            "",
            "The mappings identify protocol or asset infrastructure, not the humans or",
            "organizations that economically benefit from each mediated transaction.",
            "",
            "## Measurement rules",
            "",
            "- One address receives one incidence per Pool event even when it occupies multiple ABI roles.",
            "- Empty runtime code is reported as `code_absent_at_observed_bounds`; it is not called a person.",
            "- Equal runtime-code hashes define a technical template, never common ownership.",
            "- Only pinned primary-source labels with confidence at least 0.90 may merge addresses.",
            "- Unresolved addresses remain separate in the curated sensitivity estimate.",
            "- Collapsing every unresolved address into one entity is retained only as a mechanical extreme.",
            "- Entity metrics remain secondary until the locked coverage thresholds pass.",
            "",
            "## Reproduce",
            "",
            "A fresh clone must reproduce `real_v2` before querying the historical code layer:",
            "",
            "```bash",
            "make reproduce-real-v2",
            "make reproduce-real-v3",
            "make verify-real-v3",
            "```",
            "",
            "The second command is resumable. It writes 316 raw code batches outside Git,",
            "builds the versioned registry and weekly sensitivity panels, and performs 32",
            "independent-provider comparisons. `make verify-real-v3` is offline and validates",
            "the compact published release against its lock file.",
            "",
            "## Published files",
            "",
            "- `outputs/real_v3/ethereum/address_registry.csv.gz`: one versioned row per address;",
            "- `weekly_action_entity_sensitivity.csv`: 33 weeks × five action layers;",
            "- `weekly_entity_sensitivity.csv`: 33 all-action weekly observations;",
            "- `address_type_summary.csv` and `infrastructure_family_summary.csv`;",
            "- `cross_provider_code_checks.csv`, `code_retrieval_batches.csv`, and `manifest.json`;",
            "- `data/metadata/real_v3_ethereum_curated_labels.csv`: human-reviewable labels.",
            "",
        ]
    )
    return "\n".join(lines)


def render_latex(audit: dict[str, object]) -> str:
    summary = audit["summary"]
    weekly = audit["weekly"]
    periods = period_means(weekly)
    contract_address_share = summary["smart_contract_address_count"] / summary["address_count"]
    contract_address_percent = f"{contract_address_share:.1%}".replace("%", r"\%")
    contract_incidence_percent = f"{summary['contract_event_incidence_share']:.1%}".replace(
        "%", r"\%"
    )
    curated_incidence_percent = f"{summary['curated_label_event_incidence_coverage']:.1%}".replace(
        "%", r"\%"
    )
    lines = [
        "% Generated by scripts/render_real_v3_entity_appendix.py; do not edit by hand.",
        r"\section{Versioned contract-role and entity audit}\label{app:real-v3-entities}",
        "This release checks historical runtime code at every participant address's first",
        "and last observed blocks and applies only pinned primary-source labels. Runtime-code",
        "families measure shared technical templates, not common economic ownership.",
        "",
        r"\begin{table}[!ht]",
        r"\centering",
        r"\AVTableSetup",
        r"\caption{Ethereum contract-role and entity-label audit.}",
        r"\label{tab:real-v3-entity-audit}",
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"\textbf{Audit item} & \textbf{Value} \\",
        r"\midrule",
        rf"Participant addresses & {int(summary['address_count']):,} \\",
        (
            rf"Addresses with observed runtime code & {int(summary['smart_contract_address_count']):,} "
            rf"({contract_address_percent}) \\"
        ),
        rf"Deduplicated event--address incidences & {int(summary['event_incidence_count']):,} \\",
        (
            rf"Contract-address incidence share & "
            rf"{contract_incidence_percent} \\"
        ),
        rf"Primary-source curated labels & {int(summary['curated_label_address_count']):,} \\",
        (
            rf"Curated-label incidence coverage & "
            rf"{curated_incidence_percent} \\"
        ),
        rf"Runtime-code template families & {int(summary['infrastructure_family_count']):,} \\",
        rf"Cross-provider code checks & {int(summary['validation_check_count']):,}/{int(summary['validation_check_count']):,} \\",
        r"Economic-actor incidence coverage & 0.0\% \\",
        r"Entity coverage gate & Failed closed \\",
        r"Causal estimate produced & No \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\begin{minipage}{0.92\linewidth}",
        r"\AVTableNoteFont\emph{Notes:} Empty runtime code is not interpreted as a natural person. "
        r"Curated mappings identify protocol or asset infrastructure; they do not identify "
        r"the terminal beneficiary behind an adapter-mediated action.",
        r"\end{minipage}",
        r"\end{table}",
        "",
        r"\begin{table}[!ht]",
        r"\centering",
        r"\AVTableSetup",
        r"\caption{Unadjusted event-time measurement means.}",
        r"\label{tab:real-v3-period-means}",
        r"\begin{tabular}{@{}lrrrr@{}}",
        r"\toprule",
        r"\textbf{Period} & \textbf{Eff. addresses} & \textbf{Curated entity} & "
        r"\textbf{Contract share} & \textbf{Aave share} \\",
        r"\midrule",
    ]
    for row in periods:
        contract_share = f"{row['contract_share']:.1%}".replace("%", r"\%")
        protocol_share = f"{row['protocol_share']:.1%}".replace("%", r"\%")
        lines.append(
            f"{row['period']} & {row['effective_addresses']:.2f} & "
            f"{row['effective_curated']:.2f} & {contract_share} & "
            f"{protocol_share} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.92\linewidth}",
            r"\AVTableNoteFont\emph{Notes:} These period means are descriptive and unadjusted. "
            r"They are not event-study or difference-in-differences estimates.",
            r"\end{minipage}",
            r"\end{table}",
            r"\FloatBarrier",
            r"\input{figures/fig03_real_v3_measurement}",
            r"\FloatBarrier",
            "",
            r"Contract addresses represent only about 7.2\% of observed addresses but carry",
            r"38.1\% of event--address incidences. Ten official protocol or asset labels cover",
            r"21.9\% of incidences. This gap shows why raw address counts and terminal-user",
            "counts are not interchangeable. Because economic-actor incidence coverage is",
            r"0\%, all entity-adjusted values are sensitivities; the primary entity and",
            "causal gates remain closed.",
            "",
        ]
    )
    return "\n".join(lines)


def _line_coordinates(
    frame: pd.DataFrame,
    column: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    maximum: float,
) -> str:
    points = []
    for row in frame.itertuples(index=False):
        x = left + (float(row.event_week) + 16.0) / 32.0 * width
        value = float(getattr(row, column))
        y = top + height - min(max(value / maximum, 0.0), 1.0) * height
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def render_svg(weekly: pd.DataFrame) -> str:
    width, height = 1200, 720
    left, plot_width = 105.0, 1025.0
    top_one, panel_height = 100.0, 215.0
    top_two = 420.0
    effective_max = math.ceil(
        max(
            weekly["effective_active_addresses"].max(),
            weekly["effective_curated_entities_sensitivity"].max(),
        )
        / 20
    ) * 20
    share_max = 0.60
    effective_address_points = _line_coordinates(
        weekly,
        "effective_active_addresses",
        left=left,
        top=top_one,
        width=plot_width,
        height=panel_height,
        maximum=effective_max,
    )
    effective_entity_points = _line_coordinates(
        weekly,
        "effective_curated_entities_sensitivity",
        left=left,
        top=top_one,
        width=plot_width,
        height=panel_height,
        maximum=effective_max,
    )
    contract_points = _line_coordinates(
        weekly,
        "contract_incidence_share",
        left=left,
        top=top_two,
        width=plot_width,
        height=panel_height,
        maximum=share_max,
    )
    protocol_points = _line_coordinates(
        weekly,
        "protocol_infrastructure_incidence_share",
        left=left,
        top=top_two,
        width=plot_width,
        height=panel_height,
        maximum=share_max,
    )
    treatment_x = left + 16 / 32 * plot_width
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Address breadth and contract-incidence sensitivity across event time</title>',
        '<desc id="desc">Descriptive Ethereum Aave V3 activity breadth and infrastructure-incidence sensitivity measures around GHO activation.</desc>',
        '<rect width="1200" height="720" fill="#ffffff"/>',
        '<text x="600" y="42" text-anchor="middle" font-family="Latin Modern Roman, serif" font-size="26" font-weight="700" fill="#17324D">Address breadth and contract-incidence sensitivity across event time</text>',
        '<text x="600" y="70" text-anchor="middle" font-family="Latin Modern Roman, serif" font-size="15" fill="#5D6B78">Descriptive Ethereum Aave V3 measurements; event week 0 is GHO activation, not a causal estimate</text>',
    ]
    for top in (top_one, top_two):
        parts.append(
            f'<rect x="{left}" y="{top}" width="{plot_width}" height="{panel_height}" rx="8" fill="#F4F7FA" stroke="#C8D2DC"/>'
        )
        parts.append(
            f'<line x1="{treatment_x:.2f}" y1="{top}" x2="{treatment_x:.2f}" y2="{top + panel_height}" stroke="#6857c7" stroke-width="2" stroke-dasharray="7 6"/>'
        )
    parts.extend(
        [
            f'<text x="{left}" y="92" font-family="Latin Modern Roman, serif" font-size="17" font-weight="700" fill="#17324D">A. Effective activity breadth</text>',
            f'<polyline points="{effective_address_points}" fill="none" stroke="#2f6b9a" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>',
            f'<polyline points="{effective_entity_points}" fill="none" stroke="#6857c7" stroke-width="4" stroke-dasharray="10 6" stroke-linejoin="round" stroke-linecap="round"/>',
            f'<text x="{left}" y="412" font-family="Latin Modern Roman, serif" font-size="17" font-weight="700" fill="#17324D">B. Infrastructure incidence shares</text>',
            f'<polyline points="{contract_points}" fill="none" stroke="#147d78" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>',
            f'<polyline points="{protocol_points}" fill="none" stroke="#c46b1a" stroke-width="4" stroke-dasharray="12 5 3 5" stroke-linejoin="round" stroke-linecap="round"/>',
        ]
    )
    for top, maximum, percent in (
        (top_one, effective_max, False),
        (top_two, share_max, True),
    ):
        for fraction in (0.0, 0.5, 1.0):
            y = top + panel_height - fraction * panel_height
            label = f"{fraction * maximum:.0%}" if percent else f"{fraction * maximum:.0f}"
            parts.append(
                f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" stroke="#DCE3EA"/>'
            )
            parts.append(
                f'<text x="{left - 14}" y="{y + 5:.2f}" text-anchor="end" font-family="Latin Modern Roman, serif" font-size="13" fill="#5D6B78">{label}</text>'
            )
    for week in (-16, -8, 0, 8, 16):
        x = left + (week + 16) / 32 * plot_width
        parts.append(
            f'<text x="{x:.2f}" y="667" text-anchor="middle" font-family="Latin Modern Roman, serif" font-size="14" fill="#5D6B78">{week:+d}</text>'
        )
    parts.extend(
        [
            '<text x="600" y="700" text-anchor="middle" font-family="Latin Modern Roman, serif" font-size="15" fill="#17324D">Event week relative to governance-controlled GHO activation</text>',
            f'<text x="{treatment_x + 8:.2f}" y="120" font-family="Latin Modern Roman, serif" font-size="13" fill="#6857c7">activation</text>',
            '<line x1="720" y1="88" x2="755" y2="88" stroke="#2f6b9a" stroke-width="4"/><text x="765" y="93" font-family="Latin Modern Roman, serif" font-size="13" fill="#17324D">effective addresses</text>',
            '<line x1="905" y1="88" x2="940" y2="88" stroke="#6857c7" stroke-width="4" stroke-dasharray="10 6"/><text x="950" y="93" font-family="Latin Modern Roman, serif" font-size="13" fill="#17324D">curated-entity sensitivity</text>',
            '<line x1="750" y1="408" x2="785" y2="408" stroke="#147d78" stroke-width="4"/><text x="795" y="413" font-family="Latin Modern Roman, serif" font-size="13" fill="#17324D">all contract addresses</text>',
            '<line x1="950" y1="408" x2="985" y2="408" stroke="#c46b1a" stroke-width="4" stroke-dasharray="12 5 3 5"/><text x="995" y="413" font-family="Latin Modern Roman, serif" font-size="13" fill="#17324D">curated Aave infrastructure</text>',
            "</svg>",
        ]
    )
    return "\n".join(parts)


def _tikz_coordinates(
    frame: pd.DataFrame, column: str, *, x_scale: float, y_scale: float
) -> str:
    return " ".join(
        f"({(row.event_week + 16) * x_scale:.4f},{float(getattr(row, column)) * y_scale:.4f})"
        for row in frame.itertuples(index=False)
    )


def render_tikz(weekly: pd.DataFrame) -> str:
    maximum = math.ceil(
        max(
            weekly["effective_active_addresses"].max(),
            weekly["effective_curated_entities_sensitivity"].max(),
        )
        / 20
    ) * 20
    x_scale = 0.39
    effective_scale = 3.2 / maximum
    share_scale = 3.2 / 0.60
    address_coordinates = _tikz_coordinates(
        weekly, "effective_active_addresses", x_scale=x_scale, y_scale=effective_scale
    )
    entity_coordinates = _tikz_coordinates(
        weekly,
        "effective_curated_entities_sensitivity",
        x_scale=x_scale,
        y_scale=effective_scale,
    )
    contract_coordinates = _tikz_coordinates(
        weekly, "contract_incidence_share", x_scale=x_scale, y_scale=share_scale
    )
    protocol_coordinates = _tikz_coordinates(
        weekly,
        "protocol_infrastructure_incidence_share",
        x_scale=x_scale,
        y_scale=share_scale,
    )
    treatment_x = 16 * x_scale
    return "\n".join(
        [
            "% Generated by scripts/render_real_v3_entity_appendix.py; do not edit by hand.",
            r"\begin{figure}[htbp]",
            r"\centering",
            r"\suspendrefereelines",
            r"\definecolor{AddressBlue}{HTML}{2F6B9A}",
            r"\definecolor{EntityCoral}{HTML}{6857C7}",
            r"\definecolor{ContractTeal}{HTML}{147D78}",
            r"\definecolor{ProtocolPurple}{HTML}{C46B1A}",
            r"\begin{tikzpicture}[x=.935cm,y=1cm,font=\AVFigureFont]",
            r"\path[use as bounding box] (-0.55,-5.55) rectangle (12.85,4.35);",
            r"\draw[AddressBlue,very thick] (6.85,4.05)--(7.45,4.05);",
            r"\node[av/legend label] at (7.63,4.05) {addresses};",
            r"\draw[EntityCoral,very thick,dashed] (9.15,4.05)--(9.75,4.05);",
            r"\node[av/legend label] at (9.93,4.05) {curated sensitivity};",
            r"\node[av/panel title] at (0,3.62) {A. Effective activity breadth};",
            r"\draw[gray!45] (0,0) rectangle (12.48,3.2);",
            rf"\draw[ProtocolPurple,dashed] ({treatment_x:.2f},0) -- ({treatment_x:.2f},3.2);",
            rf"\draw[AddressBlue,very thick] plot[smooth] coordinates {{{address_coordinates}}};",
            rf"\draw[EntityCoral,very thick,dashed] plot[smooth] coordinates {{{entity_coordinates}}};",
            rf"\node[anchor=east] at (-0.12,0) {{0}}; \node[anchor=east] at (-0.12,1.6) {{{maximum / 2:.0f}}}; \node[anchor=east] at (-0.12,3.2) {{{maximum:.0f}}};",
            r"\begin{scope}[yshift=-4.75cm]",
            r"\draw[ContractTeal,very thick] (6.30,4.05)--(6.90,4.05);",
            r"\node[av/legend label] at (7.08,4.05) {contracts};",
            r"\draw[ProtocolPurple,very thick,dash dot] (9.45,4.05)--(10.05,4.05);",
            r"\node[av/legend label] at (10.23,4.05) {curated labels};",
            r"\node[av/panel title] at (0,3.62) {B. Contract and curated-label incidence};",
            r"\draw[gray!45] (0,0) rectangle (12.48,3.2);",
            rf"\draw[ProtocolPurple,dashed] ({treatment_x:.2f},0) -- ({treatment_x:.2f},3.2);",
            rf"\draw[ContractTeal,very thick] plot[smooth] coordinates {{{contract_coordinates}}};",
            rf"\draw[ProtocolPurple,very thick,dash dot] plot[smooth] coordinates {{{protocol_coordinates}}};",
            r"\node[anchor=east] at (-0.12,0) {0\%}; \node[anchor=east] at (-0.12,1.6) {30\%}; \node[anchor=east] at (-0.12,3.2) {60\%};",
            r"\foreach \x/\label in {0/-16,3.12/-8,6.24/0,9.36/+8,12.48/+16}{\draw (\x,0)--(\x,-0.08) node[below=0.8mm]{\label};}",
            r"\node at (6.24,-0.65) {Event week relative to GHO activation};",
            r"\end{scope}",
            r"\end{tikzpicture}",
            r"\resumerefereelines",
            r"\caption{Address breadth and contract-incidence sensitivity across event time.}",
            r"\label{fig:real-v3-measurement}",
            r"\begin{minipage}{0.92\linewidth}",
            r"\AVFigureSmallFont\emph{Notes:} Curated sensitivity collapses only primary-source "
            r"protocol/asset labels and leaves all unresolved addresses separate. Contract "
            r"and Aave shares are event--address incidence shares. Lines are descriptive.",
            r"\end{minipage}",
            r"\end{figure}",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render the audited Ethereum real_v3 documentation and paper appendix"
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    audit = load_audit(root)
    docs_figure = root / "docs/figures/real_v3_measurement.svg"
    docs_figure.parent.mkdir(parents=True, exist_ok=True)
    docs_figure.write_text(render_svg(audit["weekly"]), encoding="utf-8")
    (root / "docs/REAL_V3_ENTITY_LAYER.md").write_text(
        render_markdown(audit), encoding="utf-8"
    )
    (root / "paper/appendix/real_v3_entity_audit.tex").write_text(
        render_latex(audit), encoding="utf-8"
    )
    (root / "paper/figures/fig03_real_v3_measurement.tex").write_text(
        render_tikz(audit["weekly"]), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
