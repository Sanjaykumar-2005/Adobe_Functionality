"""
model.py — the intermediate Document Model.

The converter never writes a .docx straight from extracted text. Instead the
layout engine builds this in-memory model (a faithful, engine-agnostic
description of the whole document), and the Word generator renders the model.
This separation is the core of the architecture:

    PDF -> Reader -> Layout Extraction -> [Document Model] -> Layout Engine
        -> Word Generator -> DOCX

Keeping the model independent of both PyMuPDF and python-docx means the same
model can later feed a PDF->Excel or PDF->PowerPoint generator (extensibility).

All coordinates/sizes are in PDF points (1/72"). Colors are (r, g, b) 0-255.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union

BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1), origin top-left
Color = tuple[int, int, int]


class Alignment(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class ListType(str, Enum):
    BULLET = "bullet"
    NUMBER = "number"


class DrawingKind(str, Enum):
    HLINE = "hline"
    VLINE = "vline"
    RECT = "rect"
    FILLED_RECT = "filled_rect"


@dataclass
class Style:
    """Character-level formatting shared by a run of text."""
    font: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: Color = (0, 0, 0)


@dataclass
class Run:
    """A contiguous piece of text with a single style (and optional hyperlink)."""
    text: str
    style: Style = field(default_factory=Style)
    href: Optional[str] = None


@dataclass
class Paragraph:
    """A reconstructed paragraph: merged lines/spans sharing layout + style."""
    runs: list[Run] = field(default_factory=list)
    alignment: Optional[Alignment] = None
    heading_level: Optional[int] = None            # 1..6 or None
    list_type: Optional[ListType] = None
    list_level: int = 0
    left_indent: float = 0.0                        # points beyond the page margin
    space_before: float = 0.0                       # points (from whitespace analysis)
    space_after: float = 0.0                        # points
    line_gap: float = 0.0                           # median inter-line gap (points)
    is_toc: bool = False                            # render with dot-leader tab stop
    toc_page: Optional[str] = None                  # trailing page number for a TOC row
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Table:
    """A detected table: a grid of cell strings plus best-effort merge spans."""
    rows: list[list[str]] = field(default_factory=list)
    col_spans: list[list[int]] = field(default_factory=list)  # per-cell horizontal span
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    has_borders: bool = True


@dataclass
class Image:
    """An embedded raster image."""
    data: bytes = b""
    ext: str = "png"
    width: float = 0.0                              # points
    height: float = 0.0
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)


@dataclass
class Drawing:
    """A vector primitive (rule line / rectangle) worth recreating."""
    kind: DrawingKind = DrawingKind.HLINE
    bbox: BBox = (0.0, 0.0, 0.0, 0.0)
    color: Optional[Color] = None
    fill: Optional[Color] = None
    width: float = 1.0

    @property
    def top(self) -> float:
        return self.bbox[1]


# A page element is any renderable placed in reading order.
Element = Union[Paragraph, Table, Image, Drawing]


@dataclass
class Page:
    """One PDF page == one Word page. Elements are in final reading order."""
    number: int                                     # 1-based
    width: float
    height: float
    rotation: int = 0
    margin_left: float = 72.0
    margin_top: float = 72.0
    margin_right: float = 72.0
    margin_bottom: float = 72.0
    elements: list[Element] = field(default_factory=list)


@dataclass
class Document:
    """The complete intermediate model handed to the Word generator."""
    metadata: dict = field(default_factory=dict)
    pages: list[Page] = field(default_factory=list)  # may be empty when streaming
    header_text: Optional[str] = None
    footer_text: Optional[str] = None
    page_width: float = 595.32                        # A4 default (points)
    page_height: float = 841.92
    landscape: bool = False
    margin_left: float = 72.0
    margin_top: float = 72.0
    margin_right: float = 72.0
    margin_bottom: float = 72.0
