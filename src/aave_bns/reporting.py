from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

LATEX_ESCAPES = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
}


def tex_escape(value: object) -> str:
    text = str(value)
    for raw, escaped in LATEX_ESCAPES.items():
        text = text.replace(raw, escaped)
    return text


def write_treatment_table(registry_path: str | Path, output_path: str | Path) -> Path:
    registry = pd.read_csv(registry_path, keep_default_na=False)
    lines = [
        r"\begin{table}[t]",
        r"\caption{Verified protocol-policy event registry used to define candidate treatments.}",
        r"\label{tab:policy-events}",
        r"\centering",
        r"\AVTableSetup",
        r"\begin{tabularx}{\textwidth}{p{2.45cm}p{2.05cm}p{2.05cm}X p{2.05cm}}",
        r"\toprule",
        r"\textbf{Event} & \textbf{Unit} & \textbf{Activation clock} & "
        r"\textbf{Economic mechanism} & \textbf{Evidence status} \\",
        r"\midrule",
    ]
    for row in registry.itertuples(index=False):
        mechanism = tex_escape(row.mechanism)
        lines.append(
            f"{tex_escape(getattr(row, 'event_name', row.event_id))} & {tex_escape(row.unit)} & "
            f"{tex_escape(row.activation_date)} & {mechanism} & "
            f"{tex_escape(row.verification_status)} \\\\"
        )
    lines.extend([
        r"\bottomrule",
        r"\end{tabularx}",
        r"\begin{tablenotes}\AVTableNoteFont",
        (
            r"\item \emph{Notes:} Announcement, execution, technical readiness, first use, "
            r"and publicity are separate records. Primary clocks use successful governance "
            r"execution; exact blocks and UTC timestamps remain in the event-source audit."
        ),
        r"\end{tablenotes}",
        r"\end{table}",
        "",
    ])
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def write_demo_result_table(result_path: str | Path, output_path: str | Path) -> Path:
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    lines = [
        r"\begin{table}[t]",
        r"\caption{Synthetic continuous-integration check (not an empirical result).}",
        r"\label{tab:synthetic-ci}",
        r"\centering",
        r"\begin{tabular}{lr}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
        f"Outcome & {tex_escape(result['outcome'])} \\\\ ",
        f"Difference-in-differences & {result['estimate']:.4f} \\\\ ",
        f"Observations & {int(result['n_observations'])} \\\\ ",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
        "",
    ]
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def write_simulation_result_table(
    results: pd.DataFrame,
    output_path: str | Path,
) -> Path:
    required = {
        "scenario_id",
        "scenario",
        "active_entities",
        "total_activity",
        "activity_hhi",
        "chain_hhi",
        "structural_hhi",
        "max_route_removal_loss",
        "beta",
    }
    missing = required.difference(results.columns)
    if missing:
        raise ValueError(f"Simulation table is missing columns: {sorted(missing)}")
    baseline_rows = results[results["scenario_id"] == "ethereum_aave"]
    if len(baseline_rows) != 1:
        raise ValueError("Expected exactly one reference-beta Ethereum benchmark")
    baseline_activity = float(baseline_rows.iloc[0]["total_activity"])
    beta = float(results.iloc[0]["beta"])
    display_names = {
        "ethereum_aave": "Baseline",
        "ethereum_gho": "GHO issuance",
        "crosschain_single": "One route",
        "crosschain_redundant": "Two routes",
    }

    lines = [
        r"\begin{table}[t]",
        r"\caption{Stylized network-game mechanism check at the reference parameter set.}",
        r"\label{tab:simulation-mechanism}",
        r"\centering",
        r"\AVTableSetup",
        r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrrrrr@{}}",
        r"\toprule",
        r"& \multicolumn{2}{c}{\textbf{Breadth and scale}} & "
        r"\multicolumn{3}{c}{\textbf{Concentration}} & \textbf{Resilience} \\",
        r"\cmidrule(lr){2-3}\cmidrule(lr){4-6}\cmidrule(l){7-7}",
        r"\textbf{Scenario} & $N_{+}$ & $X/X_{0}$ & $H_x$ & $H_c$ & $H_s$ & $L_{\max}$ \\",
        r"\midrule",
    ]
    for row in results.itertuples(index=False):
        activity_index = float(row.total_activity) / baseline_activity
        scenario = display_names.get(row.scenario_id, row.scenario)
        lines.append(
            f"{tex_escape(scenario)} & {int(row.active_entities)} & "
            f"{activity_index:.3f} & {float(row.activity_hhi):.3f} & "
            f"{float(row.chain_hhi):.3f} & {float(row.structural_hhi):.3f} & "
            f"{float(row.max_route_removal_loss):.3f} \\\\"
        )
    lines.extend([
        r"\bottomrule",
        r"\end{tabular*}",
        r"\begin{tablenotes}\AVTableNoteFont",
        (
            rf"\item \emph{{Notes:}} Deterministic synthetic mechanism check with "
            rf"$\beta={beta:.2f}$. $N_{{+}}$ is active entities; "
            r"$X/X_0$ is activity relative to the benchmark; "
            r"$H_x$, $H_c$, and $H_s$ are activity, chain, and structural HHI; "
            r"$L_{\max}$ is the activity share lost with the largest route. "
            r"Values are not empirical estimates."
        ),
        r"\end{tablenotes}",
        r"\end{table}",
        "",
    ])
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
