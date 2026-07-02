"""
utils.py — shared geometry, colour and font helpers for the pdf2word engine.

Small, pure, dependency-light functions used across the reader, layout engine
and word generator. No PyMuPDF/python-docx imports here so the helpers stay
trivially testable.
"""
from __future__ import annotations

from .model import BBox, Color

# PyMuPDF text-span "flags" bitmask (see PyMuPDF docs).
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIF = 1 << 2
FLAG_MONO = 1 << 3
FLAG_BOLD = 1 << 4

PT_TO_EMU = 12700  # 1 point = 12700 English Metric Units (python-docx unit)


def clean_font_name(name: str) -> str:
    """Strip a PDF subset prefix + style suffix: 'ABCDEF+Arial-BoldMT' -> 'Arial'."""
    if not name:
        return ""
    if "+" in name:
        name = name.split("+", 1)[1]
    for sep in ("-", ","):
        if sep in name:
            name = name.split(sep, 1)[0]
    # Drop trailing 'MT'/'PS' foundry tags common in embedded fonts.
    for tag in ("MT", "PS"):
        if name.endswith(tag) and len(name) > len(tag) + 1:
            name = name[: -len(tag)]
    return name.strip()


def rgb_from_int(color: int | None) -> Color:
    """Convert a PyMuPDF integer colour (0xRRGGBB) into an (r, g, b) tuple."""
    if color is None:
        return (0, 0, 0)
    try:
        return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)
    except Exception:
        return (0, 0, 0)


def rgb_from_float(seq) -> Color | None:
    """Convert a 3-float [0..1] colour (PyMuPDF drawings) to (r, g, b)."""
    if not seq:
        return None
    try:
        return tuple(max(0, min(255, int(round(c * 255)))) for c in seq[:3])  # type: ignore
    except Exception:
        return None


def span_bold_italic(span: dict) -> tuple[bool, bool]:
    """Best-effort bold/italic from a span's flags and font name."""
    flags = span.get("flags", 0) or 0
    name = (span.get("font") or "").lower()
    bold = bool(flags & FLAG_BOLD) or any(k in name for k in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & FLAG_ITALIC) or "italic" in name or "oblique" in name
    return bold, italic


def bbox_center(b: BBox) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def point_in_bbox(x: float, y: float, b: BBox, pad: float = 1.0) -> bool:
    return (b[0] - pad) <= x <= (b[2] + pad) and (b[1] - pad) <= y <= (b[3] + pad)


def point_in_any(x: float, y: float, boxes: list[BBox], pad: float = 1.0) -> bool:
    return any(point_in_bbox(x, y, b, pad) for b in boxes)


def overlap_ratio(inner: BBox, outer: BBox) -> float:
    """Fraction of *inner*'s area that lies inside *outer* (0..1)."""
    ix0 = max(inner[0], outer[0])
    iy0 = max(inner[1], outer[1])
    ix1 = min(inner[2], outer[2])
    iy1 = min(inner[3], outer[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0
