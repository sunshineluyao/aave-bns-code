#!/usr/bin/env python3
"""Fail-closed geometric QA for rendered PDF figures.

The audit works on the final PDF, not on source coordinates.  It detects text/text
collisions, undersized gaps between independently positioned labels, stroked vector
paths entering protected text boxes, captions placed too close to figure content, and
fonts below the configured floor.  Filled backgrounds are intentionally ignored; their
stroked borders remain auditable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz

CAPTION_RE = re.compile(r"^(?:Fig\.|Figure)\s*\d+\b", re.IGNORECASE)
DEFAULT_MIN_FONT_SIZE = 7.0
DEFAULT_MIN_TEXT_GAP = 1.0
DEFAULT_TEXT_PADDING = 0.75
DEFAULT_CAPTION_GAP = 4.0


@dataclass(frozen=True)
class TextLine:
    page: int
    block: int
    line: int
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float

    @property
    def rect(self) -> fitz.Rect:
        return fitz.Rect(self.bbox)


@dataclass(frozen=True)
class VectorPrimitive:
    page: int
    path: int
    item: int
    bbox: tuple[float, float, float, float]
    filled: bool = False

    @property
    def rect(self) -> fitz.Rect:
        return fitz.Rect(self.bbox)


@dataclass(frozen=True)
class Issue:
    page: int
    kind: str
    message: str
    bbox: tuple[float, float, float, float] | None = None
    other_bbox: tuple[float, float, float, float] | None = None


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    if not spec:
        return list(range(page_count))
    pages: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            first, last = (int(value) for value in token.split("-", 1))
            if first > last:
                first, last = last, first
            pages.update(range(first - 1, last))
        else:
            pages.add(int(token) - 1)
    invalid = sorted(page + 1 for page in pages if page < 0 or page >= page_count)
    if invalid:
        raise ValueError(f"page(s) outside PDF: {invalid}")
    return sorted(pages)


def _text_lines(page: fitz.Page) -> list[TextLine]:
    result: list[TextLine] = []
    payload = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for block_index, block in enumerate(payload.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text or not spans:
                continue
            effective_size = max(round(float(span.get("size", 0.0)), 2) for span in spans)
            result.append(
                TextLine(
                    page=page.number + 1,
                    block=block_index,
                    line=line_index,
                    text=text,
                    bbox=tuple(round(float(value), 3) for value in line["bbox"]),
                    # TeX math subscripts are intentionally smaller than the main
                    # label. Audit the largest span so a legal subscript does not
                    # make an otherwise readable math label fail the type floor.
                    font_size=effective_size,
                )
            )
    return result


def _segment_rect(first: fitz.Point, second: fitz.Point, width: float) -> fitz.Rect:
    radius = max(0.35, width / 2.0)
    return fitz.Rect(
        min(first.x, second.x) - radius,
        min(first.y, second.y) - radius,
        max(first.x, second.x) + radius,
        max(first.y, second.y) + radius,
    )


def _cubic_points(points: tuple[fitz.Point, ...], steps: int = 12) -> Iterable[fitz.Point]:
    if len(points) != 4:
        return []
    p0, p1, p2, p3 = points
    sampled: list[fitz.Point] = []
    for index in range(steps + 1):
        t = index / steps
        u = 1.0 - t
        sampled.append(
            fitz.Point(
                u**3 * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t**3 * p3.x,
                u**3 * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t**3 * p3.y,
            )
        )
    return sampled


def _vector_primitives(page: fitz.Page) -> list[VectorPrimitive]:
    primitives: list[VectorPrimitive] = []
    for path_index, path in enumerate(page.get_drawings()):
        path_rect = fitz.Rect(path.get("rect") or fitz.Rect())
        if path.get("fill") is not None and not path_rect.is_empty:
            primitives.append(
                VectorPrimitive(
                    page=page.number + 1,
                    path=path_index,
                    item=-1,
                    bbox=tuple(round(float(value), 3) for value in path_rect),
                    filled=True,
                )
            )
        if path.get("color") is None or float(path.get("width") or 0.0) <= 0:
            continue
        width = float(path.get("width") or 0.7)
        for item_index, item in enumerate(path.get("items", [])):
            kind = item[0]
            segments: list[tuple[fitz.Point, fitz.Point]] = []
            if kind == "l":
                segments.append((item[1], item[2]))
            elif kind == "re":
                rect = fitz.Rect(item[1])
                points = (rect.tl, rect.tr, rect.br, rect.bl, rect.tl)
                segments.extend(zip(points[:-1], points[1:], strict=True))
            elif kind == "qu":
                quad = fitz.Quad(item[1])
                points = (quad.ul, quad.ur, quad.lr, quad.ll, quad.ul)
                segments.extend(zip(points[:-1], points[1:], strict=True))
            elif kind == "c":
                sampled = list(_cubic_points(tuple(item[1:])))
                segments.extend(zip(sampled[:-1], sampled[1:], strict=True))
            for first, second in segments:
                rect = _segment_rect(first, second, width)
                primitives.append(
                    VectorPrimitive(
                        page=page.number + 1,
                        path=path_index,
                        item=item_index,
                        bbox=tuple(round(float(value), 3) for value in rect),
                    )
                )
    return primitives


def _intersects(first: fitz.Rect, second: fitz.Rect) -> bool:
    overlap = first & second
    return not overlap.is_empty and overlap.width > 0 and overlap.height > 0


def _expanded(rect: fitz.Rect, amount: float) -> fitz.Rect:
    return fitz.Rect(rect.x0 - amount, rect.y0 - amount, rect.x1 + amount, rect.y1 + amount)


def _figure_scopes(
    page: fitz.Page, lines: list[TextLine], figure_regions: bool
) -> list[tuple[fitz.Rect, list[TextLine]]]:
    captions = sorted(
        (line for line in lines if CAPTION_RE.match(line.text)), key=lambda line: line.rect.y0
    )
    if not figure_regions or not captions:
        return [(page.rect, captions)]
    scopes: list[tuple[fitz.Rect, list[TextLine]]] = []
    top = page.rect.y0
    for caption in captions:
        # Segment the page at every caption. This audits multiple stacked figures
        # instead of silently stopping after the first one.
        bottom = min(page.rect.y1, caption.rect.y1 + 1.0)
        scopes.append((fitz.Rect(page.rect.x0, top, page.rect.x1, bottom), [caption]))
        top = bottom
    return scopes


def _audit_scope(
    lines: list[TextLine],
    primitives: list[VectorPrimitive],
    captions: list[TextLine],
    *,
    min_font_size: float,
    min_text_gap: float,
    text_padding: float,
    caption_gap: float,
) -> list[Issue]:
    issues: list[Issue] = []

    for line in lines:
        if line.font_size + 0.05 < min_font_size:
            issues.append(
                Issue(
                    page=line.page,
                    kind="font_floor",
                    message=(
                        f"{line.font_size:.2f} pt text is below the "
                        f"{min_font_size:.2f} pt floor: {line.text[:80]!r}"
                    ),
                    bbox=line.bbox,
                )
            )

    for index, first in enumerate(lines):
        for second in lines[index + 1 :]:
            if first.block == second.block:
                continue
            a, b = first.rect, second.rect
            x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            y_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            if x_overlap > 0 and y_overlap > 0:
                issues.append(
                    Issue(
                        page=first.page,
                        kind="text_overlap",
                        message=f"text boxes overlap: {first.text[:55]!r} / {second.text[:55]!r}",
                        bbox=first.bbox,
                        other_bbox=second.bbox,
                    )
                )
                continue
            if x_overlap > 0:
                gap = max(a.y0, b.y0) - min(a.y1, b.y1)
                if -1e-6 <= gap < min_text_gap:
                    issues.append(
                        Issue(
                            page=first.page,
                            kind="vertical_text_gap",
                            message=(
                                f"{gap:.2f} pt vertical gap is below {min_text_gap:.2f} pt: "
                                f"{first.text[:45]!r} / {second.text[:45]!r}"
                            ),
                            bbox=first.bbox,
                            other_bbox=second.bbox,
                        )
                    )
            if y_overlap > 0:
                gap = max(a.x0, b.x0) - min(a.x1, b.x1)
                if -1e-6 <= gap < min_text_gap:
                    issues.append(
                        Issue(
                            page=first.page,
                            kind="horizontal_text_gap",
                            message=(
                                f"{gap:.2f} pt horizontal gap is below {min_text_gap:.2f} pt: "
                                f"{first.text[:45]!r} / {second.text[:45]!r}"
                            ),
                            bbox=first.bbox,
                            other_bbox=second.bbox,
                        )
                    )

    # Report at most one collision per path/text pair so dense network paths do
    # not flood the result while still causing a hard failure.
    seen_path_text: set[tuple[int, int, bool]] = set()
    for line_index, line in enumerate(lines):
        protected = _expanded(line.rect, text_padding)
        for primitive in primitives:
            key = (line_index, primitive.path, primitive.filled)
            if key in seen_path_text or not _intersects(protected, primitive.rect):
                continue
            # Filled card/panel backgrounds legitimately contain their labels.
            # Ignore only a fill that fully encloses the protected text box; a
            # partially overlapping fill (for example an icon over a title) fails.
            if primitive.filled and primitive.rect.contains(line.rect):
                continue
            seen_path_text.add(key)
            kind = "fill_text_collision" if primitive.filled else "stroke_text_collision"
            description = "filled vector primitive" if primitive.filled else "stroked vector path"
            issues.append(
                Issue(
                    page=line.page,
                    kind=kind,
                    message=f"{description} enters text safety box: {line.text[:80]!r}",
                    bbox=line.bbox,
                    other_bbox=primitive.bbox,
                )
            )

    for caption in captions:
        caption_top = caption.rect.y0
        candidates: list[float] = []
        for line in lines:
            if line.block != caption.block and line.rect.y1 <= caption_top + 0.25:
                candidates.append(line.rect.y1)
        for primitive in primitives:
            if primitive.rect.y1 <= caption_top + 0.25:
                candidates.append(primitive.rect.y1)
        if candidates:
            gap = caption_top - max(candidates)
            if gap < caption_gap:
                issues.append(
                    Issue(
                        page=caption.page,
                        kind="caption_gap",
                        message=f"{gap:.2f} pt figure-to-caption gap is below {caption_gap:.2f} pt",
                        bbox=caption.bbox,
                    )
                )
    return issues


def audit_page(
    page: fitz.Page,
    *,
    figure_regions: bool,
    min_font_size: float,
    min_text_gap: float,
    text_padding: float,
    caption_gap: float,
) -> list[Issue]:
    all_lines = _text_lines(page)
    all_primitives = _vector_primitives(page)
    issues: list[Issue] = []
    for scope, captions in _figure_scopes(page, all_lines, figure_regions):
        lines = [
            line
            for line in all_lines
            if scope.y0 <= (line.rect.y0 + line.rect.y1) / 2.0 <= scope.y1
        ]
        primitives = [
            primitive for primitive in all_primitives if _intersects(primitive.rect, scope)
        ]
        issues.extend(
            _audit_scope(
                lines,
                primitives,
                captions,
                min_font_size=min_font_size,
                min_text_gap=min_text_gap,
                text_padding=text_padding,
                caption_gap=caption_gap,
            )
        )
    return list(dict.fromkeys(issues))


def audit_document(
    document: fitz.Document,
    pages: Iterable[int],
    *,
    figure_regions: bool,
    min_font_size: float,
    min_text_gap: float,
    text_padding: float,
    caption_gap: float,
) -> list[Issue]:
    issues: list[Issue] = []
    for page_index in pages:
        issues.extend(
            audit_page(
                document[page_index],
                figure_regions=figure_regions,
                min_font_size=min_font_size,
                min_text_gap=min_text_gap,
                text_padding=text_padding,
                caption_gap=caption_gap,
            )
        )
    return issues


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="pdf-layout-audit-") as temporary:
        valid_path = Path(temporary) / "valid.pdf"
        bad_path = Path(temporary) / "bad.pdf"

        valid = fitz.open()
        page = valid.new_page(width=300, height=220)
        page.insert_text((40, 45), "A. Valid panel", fontsize=10)
        page.draw_line((40, 75), (100, 75), width=1)
        page.insert_text((110, 79), "series", fontsize=8)
        page.insert_text((40, 160), "Fig. 1  Valid spacing", fontsize=9)
        valid.save(valid_path)
        valid.close()

        bad = fitz.open()
        page = bad.new_page(width=300, height=360)
        page.insert_text((40, 45), "A. First figure is clean", fontsize=10)
        page.insert_text((40, 120), "Fig. 1  Clean first caption", fontsize=9)
        page.insert_text((40, 185), "B. Second title is covered", fontsize=10)
        page.draw_circle((96, 181), 22, color=None, fill=(0.9, 0.2, 0.2))
        page.insert_text((205, 205), "tiny", fontsize=6.5)
        page.draw_line((40, 306), (170, 306), width=1)
        page.insert_text((40, 317), "Fig. 2  Tight second caption", fontsize=9)
        bad.save(bad_path)
        bad.close()

        with fitz.open(valid_path) as document:
            valid_issues = audit_document(
                document,
                [0],
                figure_regions=True,
                min_font_size=DEFAULT_MIN_FONT_SIZE,
                min_text_gap=DEFAULT_MIN_TEXT_GAP,
                text_padding=DEFAULT_TEXT_PADDING,
                caption_gap=DEFAULT_CAPTION_GAP,
            )
        if valid_issues:
            raise AssertionError(f"valid self-test fixture failed: {valid_issues}")

        with fitz.open(bad_path) as document:
            bad_issues = audit_document(
                document,
                [0],
                figure_regions=True,
                min_font_size=DEFAULT_MIN_FONT_SIZE,
                min_text_gap=DEFAULT_MIN_TEXT_GAP,
                text_padding=DEFAULT_TEXT_PADDING,
                caption_gap=DEFAULT_CAPTION_GAP,
            )
        kinds = {issue.kind for issue in bad_issues}
        required = {"fill_text_collision", "font_floor", "caption_gap"}
        if not required.issubset(kinds):
            raise AssertionError(f"bad self-test fixture missed {sorted(required - kinds)}")
    print("audit_pdf_layout self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", type=Path)
    parser.add_argument("--pages", help="1-based pages, for example 4,27-29,67")
    parser.add_argument("--figure-regions", action="store_true")
    parser.add_argument("--min-font-size", type=float, default=DEFAULT_MIN_FONT_SIZE)
    parser.add_argument("--min-text-gap", type=float, default=DEFAULT_MIN_TEXT_GAP)
    parser.add_argument("--text-padding", type=float, default=DEFAULT_TEXT_PADDING)
    parser.add_argument("--caption-gap", type=float, default=DEFAULT_CAPTION_GAP)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--max-issues", type=int, default=200)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.pdf is None:
        parser.error("PDF is required unless --self-test is used")

    with fitz.open(args.pdf) as document:
        pages = parse_pages(args.pages, document.page_count)
        issues = audit_document(
            document,
            pages,
            figure_regions=args.figure_regions,
            min_font_size=args.min_font_size,
            min_text_gap=args.min_text_gap,
            text_padding=args.text_padding,
            caption_gap=args.caption_gap,
        )
    report = {
        "pdf": str(args.pdf),
        "pages": [page + 1 for page in pages],
        "thresholds_pt": {
            "min_font_size": args.min_font_size,
            "min_text_gap": args.min_text_gap,
            "text_padding": args.text_padding,
            "caption_gap": args.caption_gap,
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": [asdict(issue) for issue in issues],
    }
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if issues:
        print(f"PDF layout audit: FAIL ({len(issues)} issue(s))")
        for issue in issues[: args.max_issues]:
            print(f"  p{issue.page} [{issue.kind}] {issue.message}")
        if len(issues) > args.max_issues:
            print(f"  ... {len(issues) - args.max_issues} additional issue(s) omitted")
        return 1
    print(f"PDF layout audit: PASS ({len(pages)} page(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
