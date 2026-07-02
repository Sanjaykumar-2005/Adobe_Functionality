"""
paragraphs.py — Paragraph Builder.

Turns raw PyMuPDF spans into clean paragraphs. The rule (per the spec) is to
MERGE nearby lines/spans that share font, size, left alignment and small vertical
spacing into a single paragraph — never one paragraph per physical line.

Two stages:
  * ``extract_lines`` — read the page's ``dict``, drop anything inside a table
    region, build per-line styled runs (merging same-style spans + tagging
    hyperlinks).
  * ``ParagraphBuilder.merge`` — merge consecutive lines of one column into
    paragraphs using geometry rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .hyperlinks import href_for_span
from .model import BBox, Paragraph, Run, Style
from .utils import (
    clean_font_name, median, overlap_ratio, point_in_any, rgb_from_int,
    span_bold_italic,
)


@dataclass
class Line:
    """One physical text line: styled runs + geometry, before paragraph merging."""
    bbox: BBox
    runs: list[Run] = field(default_factory=list)
    size: float = 0.0
    x0: float = 0.0


def _build_runs(spans: list[dict], links: list[dict]) -> list[Run]:
    """Build merged, hyperlink-tagged runs from a line's spans."""
    runs: list[Run] = []
    for s in spans:
        text = s.get("text", "")
        if not text:
            continue
        bold, italic = span_bold_italic(s)
        style = Style(
            font=clean_font_name(s.get("font", "")),
            size=round(float(s.get("size", 0.0)), 1),
            bold=bold, italic=italic, underline=False,
            color=rgb_from_int(s.get("color")),
        )
        href = href_for_span(s.get("bbox"), links)
        if runs and runs[-1].style == style and runs[-1].href == href:
            runs[-1].text += text          # coalesce identical adjacent spans
        else:
            runs.append(Run(text=text, style=style, href=href))
    return runs


def extract_lines(fitz_page, exclude_boxes: list[BBox], links: list[dict]) -> list[Line]:
    """Extract styled text lines from a page, skipping anything inside a table."""
    lines: list[Line] = []
    try:
        data = fitz_page.get_text("dict")
    except Exception:
        return lines
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:            # 0 = text (images handled elsewhere)
            continue
        # Drop whole blocks that sit mostly inside a detected table.
        if exclude_boxes and any(overlap_ratio(block["bbox"], tb) > 0.5 for tb in exclude_boxes):
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            lb = line.get("bbox", block["bbox"])
            cx = (lb[0] + lb[2]) / 2.0
            cy = (lb[1] + lb[3]) / 2.0
            if point_in_any(cx, cy, exclude_boxes):   # finer per-line exclusion
                continue
            runs = _build_runs(spans, links)
            if not runs or not any(r.text.strip() for r in runs):
                continue
            size = median([float(s.get("size", 0.0)) for s in spans]) or 0.0
            lines.append(Line(bbox=tuple(lb), runs=runs, size=round(size, 1), x0=lb[0]))
    return lines


class ParagraphBuilder:
    """Merge consecutive same-column lines into paragraphs by geometry rules."""

    #: new paragraph when the vertical gap exceeds this fraction of line height
    GAP_FACTOR = 0.7
    #: left-edge tolerance (points) for "same alignment"
    X_TOLERANCE = 3.0
    #: font-size change (points) that forces a paragraph break
    SIZE_JUMP = 1.2

    def merge(self, lines: list[Line]) -> list[Paragraph]:
        """Merge *lines* (already sorted top-to-bottom within one column)."""
        paras: list[Paragraph] = []
        cur: Paragraph | None = None
        cur_x0 = cur_size = last_y1 = 0.0
        gaps: list[float] = []

        for ln in lines:
            if cur is None:
                start_new = True
            else:
                gap = ln.bbox[1] - last_y1
                line_h = max(cur_size, ln.size, 1.0)
                start_new = (
                    gap > self.GAP_FACTOR * line_h
                    or (abs(ln.x0 - cur_x0) > self.X_TOLERANCE and gap > 0.2 * line_h)
                    or abs(ln.size - cur_size) > self.SIZE_JUMP
                )
            if start_new:
                cur = Paragraph(runs=list(ln.runs), bbox=ln.bbox)
                paras.append(cur)
                cur_x0, cur_size, last_y1 = ln.x0, ln.size, ln.bbox[3]
                gaps = []
            else:
                # soft-join wrapped lines with a space so words don't glue together
                if cur.runs and cur.runs[-1].text and not cur.runs[-1].text.endswith(" "):
                    cur.runs[-1].text += " "
                cur.runs.extend(ln.runs)
                cur.bbox = (min(cur.bbox[0], ln.bbox[0]), cur.bbox[1],
                            max(cur.bbox[2], ln.bbox[2]), ln.bbox[3])
                gaps.append(ln.bbox[1] - last_y1)
                last_y1 = ln.bbox[3]
                cur.line_gap = median(gaps)
        return paras

    @staticmethod
    def dominant_size(paragraph: Paragraph) -> float:
        """Median run size of a paragraph (used by heading detection)."""
        sizes = [r.style.size for r in paragraph.runs if r.style.size]
        return median(sizes)
