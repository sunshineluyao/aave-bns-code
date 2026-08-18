# Exact SVG and TeX literals are intentionally kept as auditable source lines.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_release(root: Path) -> dict[str, object]:
    output = root / "outputs/real_v4/ethereum"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    periods = pd.read_csv(output / "period_beneficiary_bounds.csv").set_index("period")
    changes = pd.read_csv(output / "period_change_bounds.csv").set_index("assumption")
    if int(summary["event_count"]) != 118_806:
        raise ValueError("real_v4 event count drifted")
    if int(summary["beneficiary_address_count"]) != 15_351:
        raise ValueError("real_v4 beneficiary-address count drifted")
    if bool(summary["economic_actor_direction_identified"]):
        raise ValueError("real_v4 is expected to leave actor direction unidentified")
    if bool(summary["causal_estimate_produced"]):
        raise ValueError("real_v4 must not claim a causal estimate")
    return {"summary": summary, "periods": periods, "changes": changes}


def render_markdown(release: dict[str, object]) -> str:
    summary = release["summary"]
    periods = release["periods"]
    changes = release["changes"]
    pre = periods.loc["pre"]
    post = periods.loc["post"]
    stable = changes.loc["stable_address"]
    relative_change = (
        (float(post["address_proxy_hhi"]) / float(pre["address_proxy_hhi"])) - 1
    )
    lines = [
        "# Audited Ethereum `real_v4` partial-identification release",
        "",
        "## Result in one sentence",
        "",
        "The position-holder address proxy becomes less concentrated across the locked",
        "descriptive window. Period-specific actor bounds do not supply a sharp joint",
        "economic-actor change set under stable cross-period control.",
        "",
        "![Address proxy and economic-actor HHI identified sets](figures/real_v4_partial_identification.svg)",
        "",
        "## Why the observed unit changed",
        "",
        "`real_v3` counted every unique event–address incidence across actor, beneficiary, and",
        "counterparty roles. `real_v4` uses exactly one `beneficiary_address` per Pool event:",
        "`onBehalfOf` for Supply and Borrow, and `user` for Withdraw, Repay, and",
        "LiquidationCall. This is the address whose Aave position changes. It is still an",
        "address—not a person, household, institution, or verified economic actor.",
        "",
        "## Locked timing",
        "",
        f"- On-chain treatment: block **{int(summary['activation_block']):,}**, "
        f"**{summary['activation_utc']}**;",
        f"- Aave public changelog date: **{summary['public_changelog_date']}**;",
        "- the treatment clock uses the execution timestamp, while the following-day",
        "  changelog is retained as a publicity record.",
        "",
        "Primary records: [Aave GHO mainnet ARFC](https://governance.aave.com/t/arfc-gho-mainnet-launch/13574),",
        "[Aave changelog](https://aave.com/docs/resources/changelog), and the",
        "[executed Ethereum transaction](https://etherscan.io/tx/0xae8e542d4fdb5a6a33eeb129bb80f9bf23a1ceb3ef5f6caed1fd634ae3730c0b).",
        "",
        "## Main audit results",
        "",
        "| Item | Audited value |",
        "|---|---:|",
        f"| Pool events / position-holder observations | {int(summary['event_count']):,} |",
        f"| Distinct position-holder addresses | {int(summary['beneficiary_address_count']):,} |",
        f"| Contract position-holder observations | {int(summary['contract_beneficiary_event_count']):,} ({float(summary['contract_beneficiary_share']):.2%}) |",
        f"| Curated protocol/asset infrastructure observations | {int(summary['curated_infrastructure_beneficiary_event_count']):,} ({float(summary['curated_infrastructure_beneficiary_share']):.2%}) |",
        f"| Accepted economic-actor must-links | {int(summary['accepted_must_link_constraint_count'])} |",
        f"| Full-window address-proxy HHI | {float(summary['full_address_proxy_hhi']):.6f} |",
        f"| Full-window effective position-holder addresses | {float(summary['full_address_proxy_effective_number']):.2f} |",
        f"| Event-split actor HHI identified set | [{float(summary['full_event_split_hhi_lower']):.8f}, 1] |",
        f"| Stable-address actor HHI identified set | [{float(summary['full_stable_address_hhi_lower']):.6f}, 1] |",
        "| Primary entity result | **Not produced** |",
        "| Causal estimate | **Not produced** |",
        "",
        "## Pre/post descriptive pattern and identified set",
        "",
        "| Quantity | Pre weeks −16…−1 | Post weeks +1…+16 |",
        "|---|---:|---:|",
        f"| Events | {int(pre['event_count']):,} | {int(post['event_count']):,} |",
        f"| Position-holder addresses | {int(pre['beneficiary_address_count']):,} | {int(post['beneficiary_address_count']):,} |",
        f"| Address-proxy HHI | {float(pre['address_proxy_hhi']):.6f} | {float(post['address_proxy_hhi']):.6f} |",
        f"| Effective position-holder addresses | {float(pre['address_proxy_effective_number']):.2f} | {float(post['address_proxy_effective_number']):.2f} |",
        f"| Contract position-holder share | {float(pre['contract_beneficiary_share']):.2%} | {float(post['contract_beneficiary_share']):.2%} |",
        "",
        f"The address-proxy HHI changes by {float(summary['address_proxy_hhi_change']):.6f}",
        f"({relative_change:.1%}). That point comparison is valid only for addresses. Under",
        "subtracting separate stable-address period bounds gives the conservative outer",
        f"envelope **[{float(stable['change_lower']):.6f}, {float(stable['change_upper']):.6f}]**.",
        "Its endpoints may use incompatible controller partitions across periods. It is not",
        "a sharp joint change identified set, so no signed actor-change claim is made.",
        "",
        "## Three transparent assumption layers",
        "",
        "1. **Event split.** One address may represent different actors across observations",
        "   (for example, custody). The HHI set is `[1/N, 1]`.",
        "2. **Stable address.** One address has one controller within the reported group, but",
        "   a controller may use multiple addresses. The HHI set is `[address HHI, 1]`.",
        "3. **Evidence constrained.** Primary-source, high-confidence economic-actor",
        "   `must_link` relations may merge addresses. Current accepted links: **0**, so this",
        "   set equals the stable-address set.",
        "",
        "Shared bytecode, transaction similarity, co-timing, common counterparties, or model",
        "predictions are never ownership evidence. The empty actor-constraint release is a",
        "deliberate empirical result, not missing data silently filled by heuristics.",
        "",
        "## Reproduce and verify",
        "",
        "```bash",
        "make reproduce-real-v4   # requires the local real_v2 processed event file",
        "make verify-real-v4      # fully recomputes bounds from the committed compact panel",
        "```",
        "",
        "The compact two-megabyte beneficiary panel permits offline recomputation without the",
        "12 MB decoded event table. `make verify-real-v4-local` additionally proves that the",
        "compact panel is exactly derived from the locked local real_v2 input.",
        "",
        "## Interpretation guardrail",
        "",
        "These are assumption-indexed identified sets, not confidence intervals. They do not",
        "control for market growth, reserve composition, anticipation, common shocks, or",
        "selection. Therefore they are not event-study, DiD, synthetic-control, or other causal",
        "estimates.",
        "",
    ]
    return "\n".join(lines)


def render_svg(release: dict[str, object]) -> str:
    summary = release["summary"]
    changes = release["changes"]
    stable = changes.loc["stable_address"]
    pre = float(summary["pre_address_proxy_hhi"])
    post = float(summary["post_address_proxy_hhi"])
    relative_change = (post / pre) - 1
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="820" viewBox="0 0 1500 820" role="img" aria-labelledby="title desc">
<title id="title">Address proxy and economic-actor HHI identified sets</title>
<desc id="desc">The address proxy HHI falls after GHO activation, but the economic-actor change bounds include both positive and negative values.</desc>
<defs>
  <linearGradient id="header" x1="0" x2="1"><stop offset="0" stop-color="#17324d"/><stop offset="1" stop-color="#6857c7"/></linearGradient>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="5" stdDeviation="7" flood-opacity="0.13"/></filter>
</defs>
<rect width="1500" height="820" fill="#f4f7fa"/>
<rect x="40" y="30" width="1420" height="110" rx="24" fill="url(#header)"/>
<text x="750" y="78" text-anchor="middle" font-family="Arial,sans-serif" font-size="34" font-weight="700" fill="white">Addresses look more dispersed; economic actors remain unidentified</text>
<text x="750" y="114" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" fill="#e8edff">Aave Ethereum · GHO on-chain activation: 15 July 2023, 14:02:59 UTC</text>

<g filter="url(#shadow)">
  <rect x="55" y="175" width="430" height="490" rx="22" fill="white" stroke="#2f6b9a" stroke-width="2"/>
  <rect x="535" y="175" width="430" height="490" rx="22" fill="white" stroke="#6857c7" stroke-width="2"/>
  <rect x="1015" y="175" width="430" height="490" rx="22" fill="white" stroke="#147d78" stroke-width="2"/>
</g>

<text x="270" y="225" text-anchor="middle" font-family="Arial,sans-serif" font-size="26" font-weight="700" fill="#17324d">1. Observable position address</text>
<text x="270" y="265" text-anchor="middle" font-family="Arial,sans-serif" font-size="18" fill="#17324D">One beneficiary per Pool event</text>
<text x="270" y="325" text-anchor="middle" font-family="Arial,sans-serif" font-size="44" font-weight="700" fill="#17324d">{int(summary['event_count']):,}</text>
<text x="270" y="354" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#5D6B78">events</text>
<path d="M145 405 H395" stroke="#b9c8e8" stroke-width="12" stroke-linecap="round"/>
<circle cx="170" cy="405" r="13" fill="#2f6b9a"/><circle cx="355" cy="405" r="13" fill="#6857c7"/>
<text x="170" y="445" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#17324D">Pre HHI</text>
<text x="170" y="470" text-anchor="middle" font-family="Arial,sans-serif" font-size="24" font-weight="700" fill="#2f6b9a">{pre:.6f}</text>
<text x="355" y="445" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#17324D">Post HHI</text>
<text x="355" y="470" text-anchor="middle" font-family="Arial,sans-serif" font-size="24" font-weight="700" fill="#6857c7">{post:.6f}</text>
<text x="270" y="535" text-anchor="middle" font-family="Arial,sans-serif" font-size="30" font-weight="700" fill="#147d78">{relative_change:.1%}</text>
<text x="270" y="565" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#17324D">address-proxy concentration</text>
<rect x="95" y="600" width="350" height="42" rx="10" fill="#e9f2ff"/>
<text x="270" y="627" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" font-weight="700" fill="#17324d">Address result only—not a person count</text>

<text x="750" y="225" text-anchor="middle" font-family="Arial,sans-serif" font-size="26" font-weight="700" fill="#6857c7">2. Economic-actor identified set</text>
<text x="750" y="270" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#17324D">Stable single-controller-per-address assumption</text>
<line x1="590" y1="360" x2="910" y2="360" stroke="#d6c9ef" stroke-width="16" stroke-linecap="round"/>
<circle cx="592" cy="360" r="12" fill="#6857c7"/><circle cx="908" cy="360" r="12" fill="#c46b1a"/>
<text x="592" y="405" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" fill="#17324D">{float(summary['full_stable_address_hhi_lower']):.6f}</text>
<text x="908" y="405" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" fill="#17324D">1.000000</text>
<text x="750" y="458" text-anchor="middle" font-family="Arial,sans-serif" font-size="18" fill="#17324D">Post − pre actor HHI change</text>
<text x="750" y="505" text-anchor="middle" font-family="Arial,sans-serif" font-size="29" font-weight="700" fill="#6857c7">[{float(stable['change_lower']):.6f}, {float(stable['change_upper']):.6f}]</text>
<rect x="575" y="550" width="350" height="74" rx="12" fill="#f3edff"/>
<text x="750" y="582" text-anchor="middle" font-family="Arial,sans-serif" font-size="19" font-weight="700" fill="#6857c7">Crosses zero</text>
<text x="750" y="608" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#17324D">Actor direction is not identified</text>

<text x="1230" y="225" text-anchor="middle" font-family="Arial,sans-serif" font-size="26" font-weight="700" fill="#147d78">3. Evidence gate</text>
<text x="1230" y="290" text-anchor="middle" font-family="Arial,sans-serif" font-size="48" font-weight="700" fill="#147d78">0</text>
<text x="1230" y="320" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#17324D">accepted economic-actor must-links</text>
<rect x="1065" y="355" width="330" height="68" rx="12" fill="#e8f7f1"/>
<text x="1230" y="383" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" font-weight="700" fill="#147d78">Primary sources only</text>
<text x="1230" y="407" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" fill="#17324D">No behavioral or bytecode clustering</text>
<text x="1230" y="474" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" font-weight="700" fill="#17324d">23.39%</text>
<text x="1230" y="500" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" fill="#17324D">beneficiaries are contract addresses</text>
<text x="1230" y="548" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" font-weight="700" fill="#6857c7">7.43%</text>
<text x="1230" y="574" text-anchor="middle" font-family="Arial,sans-serif" font-size="15" fill="#17324D">curated protocol/asset infrastructure</text>
<rect x="1065" y="600" width="330" height="42" rx="10" fill="#fff0eb"/>
<text x="1230" y="627" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" font-weight="700" fill="#c46b1a">No causal estimate</text>

<rect x="55" y="700" width="1390" height="76" rx="18" fill="#17324d"/>
<text x="750" y="732" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" font-weight="700" fill="white">Empirical conclusion</text>
<text x="750" y="760" text-anchor="middle" font-family="Arial,sans-serif" font-size="18" fill="#e8edff">Public-chain evidence supports an address-level pattern, but not the direction of true economic-actor decentralization.</text>
</svg>'''.replace("Arial,sans-serif", "Latin Modern Roman,serif")


def render_latex(release: dict[str, object]) -> str:
    periods = release["periods"]
    pre = periods.loc["pre"]
    post = periods.loc["post"]
    full = periods.loc["full"]
    pre_contract = f"{float(pre['contract_beneficiary_share']):.2%}".replace("%", r"\%")
    post_contract = f"{float(post['contract_beneficiary_share']):.2%}".replace("%", r"\%")
    full_contract = f"{float(full['contract_beneficiary_share']):.2%}".replace("%", r"\%")
    return "\n".join(
        [
            "% Generated by scripts/render_real_v4_partial_identification.py; do not edit.",
            r"\section{Partial identification under unresolved economic ownership}\label{app:real-v4-partial-identification}",
            r"Entity resolution is an inferential problem rather than a clerical replacement of addresses with people \cite{binette2022entity}. We therefore report assumption-indexed identified sets in the sense of partial-identification analysis \cite{molinari2020partial}.",
            "",
            r"Let $j=1,\ldots,N$ index Aave Pool events and let $a(j)$ denote the event's position-holder address. For each address $a$, let $n_a=\sum_j\mathbf{1}\{a(j)=a\}$ and $q_a=n_a/N$. The economic actors induce an unobserved partition, and $H=\sum_g s_g^2$ is the actor-level event-frequency HHI.",
            "",
            r"\begin{proposition}[Sharp period-specific logical and stable-address bounds]",
            r"If each event may belong to a different underlying actor, $H\in[1/N,1]$. If each address has one controller within the reported group but a controller may operate multiple addresses, $H\in[\sum_a q_a^2,1]$. If verified must-link relations are imposed, replace addresses in the lower endpoint with their connected components.",
            r"\end{proposition}",
            r"\begin{proof}",
            r"Merging two groups with shares $x$ and $y$ changes the HHI by $(x+y)^2-x^2-y^2=2xy\geq0$. The finest admissible partition therefore attains the lower endpoint and a single group attains the upper endpoint. Both endpoints are feasible under the stated assumptions.",
            r"\end{proof}",
            "",
            r"The stable-address model is not assumption-free: a custodial address may represent many underlying users. Conversely, one actor may control many addresses. Shared runtime code, co-timing, transaction similarity, and common counterparties are not accepted as ownership evidence. These bounds are sharp within each period. Their arithmetic difference is only a conservative outer envelope unless one common cross-period controller partition is imposed and optimized jointly.",
            "",
            r"The treatment clock is the successful governance execution at block 17,699,249 on 15 July 2023 at 14:02:59 UTC. Aave's 16 July changelog entry is retained as a distinct publicity record, not substituted for the on-chain activation.",
            "",
            r"\begin{table}[!ht]",
            r"\centering",
            r"\AVTableSetup",
            r"\caption{Position-holder address measurements and actor-level HHI bounds.}",
            r"\label{tab:real-v4-bounds}",
            r"\begin{tabular}{@{}lrrr@{}}",
            r"\toprule",
            r"\textbf{Quantity} & \textbf{Pre ($-16{:}-1$)} & "
            r"\textbf{Post ($+1{:}+16$)} & \textbf{Full} \\",
            r"\midrule",
            f"Events & {int(pre['event_count']):,} & {int(post['event_count']):,} & {int(full['event_count']):,} \\\\",
            f"Position-holder addresses & {int(pre['beneficiary_address_count']):,} & {int(post['beneficiary_address_count']):,} & {int(full['beneficiary_address_count']):,} \\\\",
            f"Address-proxy HHI & {float(pre['address_proxy_hhi']):.6f} & {float(post['address_proxy_hhi']):.6f} & {float(full['address_proxy_hhi']):.6f} \\\\",
            f"Effective addresses & {float(pre['address_proxy_effective_number']):.2f} & {float(post['address_proxy_effective_number']):.2f} & {float(full['address_proxy_effective_number']):.2f} \\\\",
            f"Contract position-holder share & {pre_contract} & {post_contract} & {full_contract} \\\\",
            f"Stable-address HHI set & $[{float(pre['stable_address_hhi_lower']):.6f},1]$ & $[{float(post['stable_address_hhi_lower']):.6f},1]$ & $[{float(full['stable_address_hhi_lower']):.6f},1]$ \\\\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.94\linewidth}",
            r"\AVTableNoteFont\emph{Notes:} Percentages are position-holder-event shares. The position holder is \texttt{onBehalfOf} for Supply and Borrow and \texttt{user} for Withdraw, Repay, and LiquidationCall. The legacy machine field is \texttt{beneficiary\_address}; it is not a verified person or institution.",
            r"\end{minipage}",
            r"\end{table}",
            r"\FloatBarrier",
            r"\input{figures/fig04_real_v4_partial_identification}",
            r"\FloatBarrier",
            "",
        ]
    )


def render_figure_tex(release: dict[str, object]) -> str:
    summary = release["summary"]
    changes = release["changes"]
    stable = changes.loc["stable_address"]
    pre = float(summary["pre_address_proxy_hhi"])
    post = float(summary["post_address_proxy_hhi"])
    relative_change = (post / pre) - 1
    relative_label = f"{relative_change:.1%}".replace("%", r"\%")
    return rf'''% Generated by scripts/render_real_v4_partial_identification.py; do not edit.
\begin{{figure}}[!ht]
\centering
\begin{{tikzpicture}}[
  font=\AVFigureFont,
  panel/.style={{av/card, minimum height=38mm, text width=32mm, inner sep=2mm}},
  arrow/.style={{-{{Latex[length=2.3mm]}}, line width=0.8pt}}
]
\node[panel, draw=AVTeal, fill=AVTeal!6] (address) at (0,0) {{\textbf{{Observed address proxy}}\\118,806 position-holder events\\Pre HHI: {pre:.6f}\\Post HHI: {post:.6f}\\\textcolor{{AVTeal}}{{\bfseries {relative_label} address HHI}}}};
\node[panel, draw=AVViolet, fill=AVViolet!5] (bounds) at (4.2,0) {{\textbf{{Conservative change envelope}}\\Period bounds are sharp\\Post--pre outer envelope:\\$[{float(stable['change_lower']):.6f},{float(stable['change_upper']):.6f}]$\\\textcolor{{AVViolet}}{{\bfseries Not a joint sharp set}}}};
\node[panel, draw=AVSlate, dashed, fill=AVSlate!5] (gate) at (8.4,0) {{\textbf{{Claim gate}}\\0 accepted actor must-links\\23.39\% contract incidence\\7.43\% curated-label incidence\\\textcolor{{AVOrange}}{{\bfseries No signed actor claim}}}};
\draw[arrow, draw=AVTeal] (address) -- (bounds);
\draw[arrow, draw=AVViolet] (bounds) -- (gate);
\pic[av/icon teal,scale=.48] at ($(address.north west)+(.34,-.34)$) {{av/users}};
\pic[av/icon violet,scale=.48] at ($(bounds.north west)+(.34,-.34)$) {{av/filter}};
\pic[av/icon slate,scale=.48] at ($(gate.north west)+(.34,-.34)$) {{av/document}};
\node[av/tag, fill=AVTeal] at (2.1,2.35) {{Assumption bounds}};
\node[av/tag, fill=AVViolet] at (6.3,2.35) {{Fail-closed rule}};
\node[av/card, draw=AVRule, fill=AVLight, text width=114mm, align=center, below=8mm of bounds] {{\textbf{{Interpretation:}} the address proxy becomes less concentrated. Subtracting separate period bounds permits incompatible controller partitions, so the displayed envelope is not a sharp joint change set or a treatment effect.}};
\end{{tikzpicture}}
\caption{{Address-level pattern and economic-actor evidence boundary. The legacy beneficiary field records the Aave position-holder address, not a verified actor. Period bounds are sharp under their stated assumptions; the displayed change envelope is conservative and no signed actor-change claim is produced.}}
\label{{fig:real-v4-partial-identification}}
\end{{figure}}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Render real_v4 documentation and paper assets")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    release = load_release(root)
    files = {
        root / "docs/REAL_V4_PARTIAL_IDENTIFICATION.md": render_markdown(release),
        root / "docs/figures/real_v4_partial_identification.svg": render_svg(release),
        root / "paper/appendix/real_v4_partial_identification.tex": render_latex(release),
        root / "paper/figures/fig04_real_v4_partial_identification.tex": render_figure_tex(release),
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        print(path.relative_to(root))


if __name__ == "__main__":
    main()
