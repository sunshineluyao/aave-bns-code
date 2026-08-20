#!/usr/bin/env python3
"""Render the editable RC26 open-science pipeline from its JSON registry."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/open-science-pipeline/pipeline_manifest.json"
DEFAULT_OUTPUT = ROOT / "docs/open-science-pipeline/open_science_pipeline.svg"

INK = "#18324A"
SURFACE = "#F5F7FB"
PRIMARY = "#315EFB"
SECONDARY = "#12A594"
ACCENT = "#D97745"
DIVIDER = "#CBD5E1"
WHITE = "#FFFFFF"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text_block(
    element_id: str,
    x: int,
    y: int,
    lines: list[str],
    *,
    size: int = 20,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
    line_height: int | None = None,
    italic: bool = False,
) -> str:
    step = line_height or int(size * 1.28)
    tspans = []
    for index, line in enumerate(lines):
        dy = 0 if index == 0 else step
        tspans.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
    style = "italic" if italic else "normal"
    return (
        f'<text id="{element_id}" x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Inter, Arial, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" font-style="{style}" fill="{fill}">'
        + "".join(tspans)
        + "</text>"
    )


def panel(element_id: str, x: int, y: int, w: int, h: int, title: str, color: str) -> str:
    return f"""
    <g id="{element_id}">
      <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="24" fill="{WHITE}" stroke="{DIVIDER}" stroke-width="2"/>
      <path d="M {x+24} {y} H {x+w-24} Q {x+w} {y} {x+w} {y+24} V {y+58} H {x} V {y+24} Q {x} {y} {x+24} {y} Z" fill="{color}"/>
      {text_block(element_id+'-title', x+24, y+38, [title], size=23, weight=700, fill=WHITE)}
    </g>"""


def arrow(element_id: str, x1: int, y1: int, x2: int, y2: int, *, color: str = PRIMARY, dashed: bool = False) -> str:
    dash = ' stroke-dasharray="10 9"' if dashed else ""
    marker = "url(#arrow-accent)" if color == ACCENT else "url(#arrow-primary)"
    return (
        f'<path id="{element_id}" d="M {x1} {y1} L {x2} {y2}" fill="none" '
        f'stroke="{color}" stroke-width="4" stroke-linecap="round"{dash} marker-end="{marker}"/>'
    )


def stage_card(element_id: str, x: int, y: int, w: int, h: int, *, border: str = PRIMARY) -> str:
    return (
        f'<rect id="{element_id}" x="{x}" y="{y}" width="{w}" height="{h}" rx="18" '
        f'fill="{SURFACE}" stroke="{border}" stroke-width="2.5"/>'
    )


def icon_public(x: int, y: int) -> str:
    return f"""
    <g id="icon-public-blocks" fill="none" stroke="{PRIMARY}" stroke-width="3">
      <path d="M {x} {y+24} l32 -18 32 18 -32 18 z" fill="{WHITE}"/>
      <path d="M {x} {y+42} l32 18 32 -18 M {x} {y+58} l32 18 32 -18"/>
      <path d="M {x+82} {y+8} h50 v68 h-50 z" fill="{WHITE}"/>
      <path d="M {x+92} {y+24} h30 M {x+92} {y+38} h30 M {x+92} {y+52} h22"/>
    </g>"""


def icon_acquisition(x: int, y: int) -> str:
    return f"""
    <g id="icon-acquisition-terminal" fill="none" stroke="{PRIMARY}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <rect x="{x}" y="{y}" width="132" height="78" rx="12" fill="{WHITE}"/>
      <path d="M {x+20} {y+24} l15 14 -15 14 M {x+48} {y+52} h26"/>
      <path d="M {x+88} {y+22} q12 8 0 16 t0 16" stroke="{SECONDARY}"/>
      <circle cx="{x+114}" cy="{y+18}" r="13" fill="{SECONDARY}" stroke="none"/>
      <path d="M {x+108} {y+18} l5 5 9 -11" stroke="{WHITE}"/>
    </g>"""


def icon_folder(x: int, y: int) -> str:
    return f"""
    <g id="icon-queried-folder" fill="none" stroke="{SECONDARY}" stroke-width="3" stroke-linejoin="round">
      <path d="M {x} {y+22} h48 l12 14 h76 v62 h-136 z" fill="{WHITE}"/>
      <path d="M {x+18} {y+52} h66 M {x+18} {y+68} h78 M {x+18} {y+84} h54"/>
      <circle cx="{x+112}" cy="{y+72}" r="19" fill="{SECONDARY}" stroke="none"/>
      <path d="M {x+102} {y+72} l7 7 14 -17" stroke="{WHITE}" stroke-width="4" stroke-linecap="round"/>
    </g>"""


def icon_tables(x: int, y: int) -> str:
    return f"""
    <g id="icon-processed-tables" fill="none" stroke="{SECONDARY}" stroke-width="3">
      <rect x="{x+12}" y="{y}" width="124" height="78" rx="9" fill="{WHITE}"/>
      <path d="M {x+12} {y+24} h124 M {x+12} {y+49} h124 M {x+50} {y} v78 M {x+92} {y} v78"/>
      <path d="M {x} {y+14} v80 h124" stroke="{DIVIDER}"/>
      <path d="M {x+24} {y+12} h18 M {x+62} {y+12} h18 M {x+104} {y+12} h18" stroke="{PRIMARY}" stroke-width="5"/>
    </g>"""


def icon_shield(x: int, y: int) -> str:
    return f"""
    <g id="icon-provenance-shield" transform="translate({x} {y}) scale(0.76)" fill="none" stroke="{PRIMARY}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
      <path d="M 60 0 l52 18 v40 c0 32 -21 53 -52 69 -31 -16 -52 -37 -52 -69 v-40 z" fill="{WHITE}"/>
      <path d="M 34 44 h52 M 34 62 h52" stroke="{DIVIDER}"/>
      <path d="M 34 84 l14 14 34 -38" stroke="{SECONDARY}" stroke-width="7"/>
    </g>"""


def icon_network(x: int, y: int) -> str:
    nodes = [(x+18,y+58),(x+58,y+18),(x+62,y+94),(x+112,y+38),(x+126,y+94)]
    edges = [(0,1),(0,2),(1,3),(2,3),(2,4),(3,4)]
    parts=[f'<g id="icon-analysis-network" fill="none" stroke="{PRIMARY}" stroke-width="3">']
    for a,b in edges:
        parts.append(f'<line x1="{nodes[a][0]}" y1="{nodes[a][1]}" x2="{nodes[b][0]}" y2="{nodes[b][1]}"/>')
    for i,(cx,cy) in enumerate(nodes):
        color=SECONDARY if i in {1,3} else PRIMARY
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="11" fill="{color}" stroke="{WHITE}" stroke-width="3"/>')
    parts.append(f'<path d="M {x+5} {y+122} h134" stroke="{DIVIDER}"/><path d="M {x+18} {y+122} v-18 M {x+48} {y+122} v-36 M {x+78} {y+122} v-24 M {x+108} {y+122} v-50" stroke="{ACCENT}" stroke-width="6"/>')
    parts.append('</g>')
    return ''.join(parts)


def icon_outputs(x: int, y: int) -> str:
    return f"""
    <g id="icon-output-bundle" fill="none" stroke="{PRIMARY}" stroke-width="3">
      <path d="M {x+10} {y+16} h92 v104 h-92 z" fill="{WHITE}"/>
      <path d="M {x+24} {y+42} h58 M {x+24} {y+58} h58 M {x+24} {y+74} h42"/>
      <path d="M {x+62} {y+2} h92 v104 h-38" fill="{SURFACE}"/>
      <path d="M {x+78} {y+82} l18 -20 16 9 25 -31" stroke="{SECONDARY}" stroke-width="5"/>
      <circle cx="{x+78}" cy="{y+82}" r="4" fill="{SECONDARY}" stroke="none"/>
      <circle cx="{x+96}" cy="{y+62}" r="4" fill="{SECONDARY}" stroke="none"/>
      <circle cx="{x+112}" cy="{y+71}" r="4" fill="{SECONDARY}" stroke="none"/>
      <circle cx="{x+137}" cy="{y+40}" r="4" fill="{SECONDARY}" stroke="none"/>
    </g>"""


def icon_paper(x: int, y: int) -> str:
    return f"""
    <g id="icon-paper-stack" fill="none" stroke="{PRIMARY}" stroke-width="3">
      <path d="M {x+18} {y+8} h94 l24 24 v104 h-118 z" fill="{WHITE}"/>
      <path d="M {x+112} {y+8} v24 h24 M {x+38} {y+50} h78 M {x+38} {y+68} h78 M {x+38} {y+86} h48"/>
      <circle cx="{x+108}" cy="{y+108}" r="18" fill="{SECONDARY}" stroke="none"/>
      <path d="M {x+98} {y+108} l7 7 14 -17" stroke="{WHITE}" stroke-width="4" stroke-linecap="round"/>
    </g>"""


def evidence_ledger(x: int, y: int, w: int) -> str:
    states = [
        ("OBSERVED", "source records", SECONDARY, "solid"),
        ("DERIVED", "deterministic metrics", PRIMARY, "solid"),
        ("BOUNDED", "identified envelope", INK, "outline"),
        ("SYNTHETIC", "mechanism only", DIVIDER, "outline"),
        ("FAILED_DESIGN", "diagnostic ≠ effect", ACCENT, "cross"),
        ("BLOCKED", "claim withheld", ACCENT, "lock"),
    ]
    out=[f'<g id="hero-evidence-ledger">']
    for i,(state,note,color,kind) in enumerate(states):
        yy=y+i*34
        fill=color if kind=="solid" else WHITE
        out.append(f'<rect x="{x}" y="{yy}" width="18" height="18" rx="3" fill="{fill}" stroke="{color}" stroke-width="2"/>')
        if kind=="cross": out.append(f'<path d="M {x+4} {yy+4} l10 10 M {x+14} {yy+4} l-10 10" stroke="{color}" stroke-width="2"/>')
        if kind=="lock": out.append(f'<path d="M {x+5} {yy+8} v-3 a4 4 0 0 1 8 0 v3" stroke="{color}" stroke-width="2" fill="none"/>')
        out.append(text_block(f'ledger-state-{i}',x+30,yy+15,[state],size=16,weight=700,fill=INK))
        out.append(text_block(f'ledger-note-{i}',x+175,yy+15,[note],size=16,fill=INK))
    out.append(f'<path id="hero-ledger-boundary" d="M {x+w-8} {y-8} v204" stroke="{ACCENT}" stroke-width="3" stroke-dasharray="7 7"/>')
    out.append('</g>')
    return ''.join(out)


def publication_icons(x: int, y: int) -> str:
    return f"""
    <g id="icon-release-lock" fill="none" stroke="{ACCENT}" stroke-width="3">
      <rect x="{x}" y="{y+24}" width="48" height="42" rx="8" fill="{WHITE}"/>
      <path d="M {x+10} {y+24} v-10 a14 14 0 0 1 28 0 v10"/>
      <circle cx="{x+24}" cy="{y+45}" r="4" fill="{ACCENT}" stroke="none"/>
    </g>
    <g id="icon-release-checklist" fill="none" stroke="{INK}" stroke-width="2.5">
      <path d="M {x+72} {y} h170 v72 h-170 z" fill="{WHITE}"/>
      <path d="M {x+88} {y+18} l5 5 9 -11 M {x+88} {y+37} l5 5 9 -11 M {x+88} {y+56} h14" stroke="{SECONDARY}"/>
      <path d="M {x+112} {y+18} h108 M {x+112} {y+37} h108 M {x+112} {y+56} h108" stroke="{DIVIDER}"/>
    </g>"""


def stage(manifest: dict, stage_id: str) -> dict:
    return next(item for item in manifest["stages"] if item["id"] == stage_id)


def render(manifest: dict) -> str:
    public = stage(manifest, "public_evidence")
    acquisition = stage(manifest, "completed_acquisition")
    queried = stage(manifest, "queried_evidence")
    processed = stage(manifest, "processed_tables")
    gate = stage(manifest, "provenance_gate")
    analysis = stage(manifest, "offline_analysis")
    outputs = stage(manifest, "release_outputs")
    paper = stage(manifest, "rc26_paper")

    svg=[f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000" viewBox="0 0 1600 1000" role="img" aria-labelledby="figure-title figure-desc">
  <title id="figure-title">{esc(manifest['title'])}</title>
  <desc id="figure-desc">Evidence-bounded RC26 workflow from completed acquisition through verified data migration, offline analysis, governed outputs, and a validated paper.</desc>
  <defs>
    <marker id="arrow-primary" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{PRIMARY}"/></marker>
    <marker id="arrow-accent" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{ACCENT}"/></marker>
  </defs>
  <rect id="canvas-background" x="0" y="0" width="1600" height="1000" fill="{SURFACE}"/>
  <rect id="title-band" x="0" y="0" width="1600" height="128" fill="{WHITE}"/>
  {text_block('main-title', 800, 55, [manifest['title']], size=36, weight=750, fill=INK, anchor='middle')}
  {text_block('main-subtitle', 800, 91, [manifest['subtitle']], size=20, weight=450, fill=INK, anchor='middle')}
  {text_block('main-policy', 800, 118, ['No blockchain re-query in the RC26 release path'], size=16, weight=700, fill=SECONDARY, anchor='middle')}
''']

    svg.append(panel("panel-acquisition",50,150,1500,315,"1 · Acquisition identity and verified migration",PRIMARY))
    cards=[(public,80,226,236,196,PRIMARY,icon_public(128,250),"label-public-evidence",[public['short_label'],"Observed inputs"]),
           (acquisition,365,226,236,196,PRIMARY,icon_acquisition(416,252),"label-completed-acquisition",[acquisition['short_label'],"Pinned · read-only"]),
           (queried,650,226,236,196,SECONDARY,icon_folder(700,234),"label-queried-evidence",[queried['short_label'],"Verified copy"]),
           (processed,935,226,236,196,SECONDARY,icon_tables(985,238),"label-processed-tables",[processed['short_label'],"Status-governed"]),
           (gate,1220,226,286,196,PRIMARY,icon_shield(1318,234),"label-provenance-gate",[gate['short_label'],"Fail closed"])]
    for item,x,y,w,h,border,icon,label_id,notes in cards:
        svg.extend([stage_card('card-'+item['id'],x,y,w,h,border=border),icon,
                    text_block(label_id,x+w//2,y+130,[item['title']],size=18,weight=700,anchor='middle'),
                    text_block(label_id+'-detail',x+w//2,y+158,notes,size=15,anchor='middle',line_height=21)])
    for i,(x1,x2) in enumerate([(316,365),(601,650),(886,935),(1171,1220)]):
        svg.append(arrow(f'acquisition-arrow-{i}',x1,300,x2-12,300,color=SECONDARY if i in {1,2} else PRIMARY))
    svg.append(text_block('migration-note',770,449,['solid = implemented · double-check = verified migration · no new snapshot substituted'],size=15,italic=True,anchor='middle'))

    svg.append(panel("panel-analysis",50,495,930,420,"2 · Offline analysis and governed outputs",SECONDARY))
    lower=[(analysis,85,575,250,250,PRIMARY,icon_network(140,582),"label-offline-analysis",[analysis['short_label'],"network · HHI · bounds"]),
           (outputs,385,575,250,250,SECONDARY,icon_outputs(435,582),"label-release-outputs",[outputs['short_label'],"byte-stable snapshot"]),
           (paper,685,575,250,250,PRIMARY,icon_paper(740,570),"label-rc26-paper",[paper['short_label'],"merged · read-only"])]
    for item,x,y,w,h,border,icon,label_id,notes in lower:
        svg.extend([stage_card('card-'+item['id'],x,y,w,h,border=border),icon,
                    text_block(label_id,x+w//2,y+164,[item['title']],size=20,weight=700,anchor='middle'),
                    text_block(label_id+'-detail',x+w//2,y+196,notes,size=15,anchor='middle',line_height=22)])
    svg.append(arrow('analysis-arrow-1',335,700,373,700,color=SECONDARY))
    svg.append(arrow('analysis-arrow-2',635,700,673,700,color=PRIMARY))
    svg.append(text_block('analysis-boundary',510,862,['address ≠ actor   ·   FAILED_DESIGN ≠ causal effect   ·   HHI ≠ capital'],size=16,weight=650,fill=ACCENT,anchor='middle'))

    svg.append(panel("panel-ledger",1010,495,540,285,"3 · Claim / evidence ledger",INK))
    svg.append(text_block('label-evidence-ledger',1042,574,['Every claim carries one explicit state'],size=18,weight=700))
    svg.append(evidence_ledger(1044,586,458))

    svg.append(panel("panel-publication",1010,800,540,140,"4 · Public-release boundary",ACCENT))
    svg.append(publication_icons(1040,864))
    svg.append(text_block('publication-gate-label',1395,880,['License · reuse · privacy', 'Hub · Viewer · Croissant'],size=15,weight=650,anchor='middle',line_height=22))
    svg.append(text_block('publication-status-label',1395,929,['NOT READY FOR PUBLICATION'],size=13,weight=750,fill=ACCENT,anchor='middle'))
    svg.append(arrow('publication-dashed-edge',1490,780,1490,798,color=ACCENT,dashed=True))

    svg.append(f'<line id="footer-divider" x1="50" y1="946" x2="1550" y2="946" stroke="{DIVIDER}" stroke-width="2"/>')
    svg.append(text_block('footer-left',55,976,[f"Source {manifest['revisions']['scientific_source'][:8]} · Data {manifest['revisions']['data_candidate'][:8]} · Paper {manifest['revisions']['paper'][:8]}"],size=14,fill=INK))
    svg.append(text_block('footer-right',1545,976,[f"Registry v{manifest['schema_version']} · checked {manifest['checked_date']}"],size=14,fill=INK,anchor='end'))
    svg.append('</svg>\n')
    return ''.join(svg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(manifest), encoding="utf-8")
    print(f"wrote editable SVG: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
