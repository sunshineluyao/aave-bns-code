from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_audit(root: Path) -> dict[str, Any]:
    output = root / "outputs/real_v2/ethereum"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    contract = json.loads((output / "contract_code_check.json").read_text(encoding="utf-8"))
    boundary_checks = read_csv(output / "boundary_provider_checks.csv")
    log_checks = read_csv(output / "cross_provider_checks.csv")
    weekly = read_csv(output / "weekly_action_panel.csv")

    action_total = sum(int(value) for value in summary["action_counts"].values())
    if action_total != int(summary["event_count"]):
        raise ValueError("Action counts do not reconcile to the total event count")
    if len(weekly) != 165 or int(summary["weekly_panel_rows"]) != 165:
        raise ValueError("The weekly action panel must contain 33 weeks times five actions")
    if len(boundary_checks) != 4 or not all(
        row["exact_match"] == "True" for row in boundary_checks
    ):
        raise ValueError("Boundary-provider checks are incomplete or failed")
    if len(log_checks) != 4 or not all(row["exact_match"] == "True" for row in log_checks):
        raise ValueError("Log-provider checks are incomplete or failed")
    if contract["exact_match"] is not True:
        raise ValueError("Contract bytecode check failed")
    if manifest["causal_estimate_produced"] is not False:
        raise ValueError("The extraction manifest must not claim a causal estimate")

    ranges: dict[str, dict[str, int]] = {}
    for action in summary["action_counts"]:
        counts = [int(row["event_count"]) for row in weekly if row["action"] == action]
        ranges[action] = {"minimum": min(counts), "maximum": max(counts)}
    return {
        "summary": summary,
        "manifest": manifest,
        "contract": contract,
        "boundary_checks": boundary_checks,
        "log_checks": log_checks,
        "ranges": ranges,
    }


def render_markdown(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    manifest = audit["manifest"]
    lines = [
        "# Audited Ethereum `real_v2` extraction results",
        "",
        "## Status",
        "",
        "The symmetric Ethereum Aave V3 Pool action panel completed its extraction and",
        "cross-provider audit. It is a descriptive address-level input to the locked causal",
        "design; it is not a causal estimate, an entity panel, or a dollar-valued dataset.",
        "",
        "## Extraction audit",
        "",
        "| Item | Audited value |",
        "|---|---:|",
        (
            f"| UTC window | {summary['window']['start_utc']} to "
            f"{summary['window']['end_utc_exclusive']} (exclusive) |"
        ),
        (
            f"| Block window | {summary['window']['first_block']:,}–"
            f"{summary['window']['last_block']:,} |"
        ),
        f"| Aave V3 Pool events | {summary['event_count']:,} |",
        f"| Transactions | {summary['transaction_count']:,} |",
        f"| Distinct addresses | {summary['unique_address_count']:,} |",
        f"| Reserve addresses | {summary['reserve_count']:,} |",
        f"| Raw retrieval partitions | {summary['retrieval_chunk_count']:,} |",
        f"| Weekly action-panel rows | {summary['weekly_panel_rows']:,} |",
        f"| Reserve-week-action rows | {summary['reserve_week_panel_rows']:,} |",
        f"| Canonical raw-log SHA-256 | `{manifest['raw_log_canonical_sha256']}` |",
        "",
        "## Action composition",
        "",
        "| Action | Events | Weekly minimum | Weekly maximum |",
        "|---|---:|---:|---:|",
    ]
    for action, count in sorted(summary["action_counts"].items()):
        range_row = audit["ranges"][action]
        lines.append(
            f"| {action.title()} | {int(count):,} | {range_row['minimum']:,} | "
            f"{range_row['maximum']:,} |"
        )
    lines.extend(
        [
            "",
            "The weekly ranges are descriptive counts. They are not adjusted for market",
            "growth, common shocks, reserve composition, anticipation, or comparison groups.",
            "",
            "## Independent validation",
            "",
            (
                f"- {len(audit['boundary_checks'])} boundary block-hash and timestamp "
                "samples matched exactly."
            ),
            f"- {len(audit['log_checks'])} ten-block log samples matched exactly.",
            "- Pool bytecode at treatment block 17,699,249 matched exactly between the two",
            "  historical-state providers.",
            f"- Bytecode SHA-256: `{audit['contract']['primary_code_sha256']}`.",
            "- All 165 raw retrieval partitions were obtained from the registered primary",
            "  provider in the successful final run; no cached partition entered the manifest.",
            "",
            "## Interpretation guardrails",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in summary["limitations"])
    lines.extend(
        [
            "",
            "The next gate is versioned contract-role and entity labeling, followed by",
            "pre-trend, donor-support, anticipation, and placebo diagnostics. No causal",
            "coefficient should enter the paper before those gates pass.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "PYTHONPATH=src python scripts/run_real_v2_ethereum.py",
            "PYTHONPATH=src python scripts/render_real_v2_ethereum_appendix.py",
            "```",
            "",
            "Canonical details are in `docs/REAL_V2_ETHEREUM.md`; machine-readable outputs",
            "and hashes are under `outputs/real_v2/ethereum/`.",
            "",
        ]
    )
    return "\n".join(lines)


def render_latex(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    manifest = audit["manifest"]
    lines = [
        "% Generated by scripts/render_real_v2_ethereum_appendix.py; do not edit by hand.",
        r"\section{Audited Ethereum real-data extraction}\label{app:real-v2-ethereum}",
        (
            "The symmetric Ethereum extraction covers event weeks $-16$ through $+16$ "
            "around the governance-controlled GHO activation block. It is an address-level "
            "descriptive input, not a causal estimate or an entity-level result."
        ),
        "",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Audited Ethereum Aave V3 Pool extraction.}",
        r"\label{tab:real-v2-ethereum-audit}",
        r"\small",
        r"\AVTableSetup",
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"\textbf{Audit item} & \textbf{Value} \\",
        r"\midrule",
        rf"First block & {int(summary['window']['first_block']):,} \\",
        rf"Last block & {int(summary['window']['last_block']):,} \\",
        rf"Pool events & {int(summary['event_count']):,} \\",
        rf"Transactions & {int(summary['transaction_count']):,} \\",
        rf"Distinct addresses & {int(summary['unique_address_count']):,} \\",
        rf"Reserve addresses & {int(summary['reserve_count']):,} \\",
        rf"Raw partitions & {int(summary['retrieval_chunk_count']):,} \\",
        (
            f"Boundary checks matching & {len(audit['boundary_checks'])}/"
            rf"{len(audit['boundary_checks'])} \\"
        ),
        rf"Log samples matching & {len(audit['log_checks'])}/{len(audit['log_checks'])} \\",
        r"Causal estimate produced & No \\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
        "",
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Action composition in the locked 33-week panel.}",
        r"\label{tab:real-v2-ethereum-actions}",
        r"\small",
        r"\AVTableSetup",
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"\textbf{Action} & \textbf{Events} & \textbf{Weekly minimum} & \textbf{Weekly maximum} \\",
        r"\midrule",
    ]
    for action, count in sorted(summary["action_counts"].items()):
        range_row = audit["ranges"][action]
        lines.append(
            f"{action.title()} & {int(count):,} & {range_row['minimum']:,} & "
            f"{range_row['maximum']:,} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.90\linewidth}",
            r"\footnotesize\emph{Notes:} Counts are descriptive. Addresses are not persons "
            r"or entities; raw reserve amounts are not combined across assets or labelled USD. "
            r"No treatment effect is estimated in this extraction stage.",
            r"\end{minipage}",
            r"\end{table}",
            r"\FloatBarrier",
            "",
            "The canonical raw-log SHA-256 is",
            r"\begin{quote}\ttfamily\small "
            + manifest["raw_log_canonical_sha256"]
            + r"\end{quote}",
            "All four sampled boundary records, all four sampled log windows, and the Pool",
            "bytecode at block 17,699,249 matched across independent providers. Complete",
            "source grades, delivery limitations, public URLs, and access requirements appear",
            "in Appendix~\\ref{tab:source-catalog}.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render the audited Ethereum real_v2 GitHub and paper summaries"
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    audit = load_audit(root)
    (root / "docs/REAL_V2_ETHEREUM_RESULTS.md").write_text(render_markdown(audit), encoding="utf-8")
    (root / "paper/appendix/real_v2_ethereum_audit.tex").write_text(
        render_latex(audit), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
