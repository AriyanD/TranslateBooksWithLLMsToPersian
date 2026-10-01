"""
Shared data model and exceptions for PDF translation.

Pure data module: it must never import PyMuPDF, so that the package can be
imported (and the data model used) even when PyMuPDF is not installed.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Coarse paragraph styles produced by the extractor and consumed by the builder
PARAGRAPH_STYLES = ("heading1", "heading2", "heading3", "list", "normal", "cell")

# Generic font families a paragraph (or the whole document) can be rendered with
FONT_FAMILIES = ("serif", "sans-serif", "monospace")


@dataclass
class PdfImage:
    """An image extracted from a source PDF page."""

    data: bytes          # Encoded image bytes (png or jpeg)
    ext: str             # "png" or "jpeg" only
    width_pt: float      # Displayed width on the source page, in points
    height_pt: float     # Displayed height on the source page, in points


@dataclass(frozen=True)
class ParagraphFormat:
    """Visual attributes of one paragraph (all optional; defaults = plain paragraph)."""

    color: Optional[str] = None        # "#rrggbb" (lowercase) or None = default text colour
    font_family: Optional[str] = None  # one of FONT_FAMILIES, or None = document default
    bold: bool = False                 # every non-blank span of the paragraph is bold
    italic: bool = False               # every non-blank span of the paragraph is italic
    box: Optional[int] = None          # index into PdfPlainContent.boxes, or None


@dataclass(frozen=True)
class PdfBox:
    """A filled rectangle drawn behind one or more paragraphs (or a table header row)."""

    background: str                    # "#rrggbb"
    border_left: Optional[str] = None  # "#rrggbb" of a thin bar on the box's left edge, or None


@dataclass
class PdfTable:
    """A table whose non-empty cells are contiguous units of paragraphs_text.

    Invariants:
    - The non-None entries of rows, read row-major, are exactly
      range(first_index, first_index + n) with n >= 1; each such paragraph has style "cell".
    - Every row has the same length, and len(column_widths) == len(rows[0]).
    """

    first_index: int                   # index in paragraphs_text of the first non-empty cell
    rows: List[List[Optional[int]]]    # paragraph index per cell (row-major), None = empty cell
    header: bool = False               # rows[0] is a header row
    column_widths: List[float] = field(default_factory=list)  # relative widths, sum == 1.0


@dataclass
class PdfPlainContent:
    """Plain-text view of a PDF: ordered paragraphs, styles, formats, boxes, tables and anchored images.

    Invariants:
    - paragraphs_format is either empty (legacy / no formatting) or has the same length as
      paragraphs_text.
    - Tables are listed by increasing first_index and their paragraph index ranges never overlap.
    - Every ParagraphFormat.box is a valid index into boxes.
    """

    paragraphs_text: List[str] = field(default_factory=list)
    # Parallel to paragraphs_text, values in PARAGRAPH_STYLES
    paragraphs_style: List[str] = field(default_factory=list)
    # Images keyed by the index of the paragraph they follow (-1 = before the first paragraph)
    images_by_paragraph: Dict[int, List[PdfImage]] = field(default_factory=dict)
    # (width_pt, height_pt) of the first source page
    page_size: Optional[Tuple[float, float]] = None
    title: str = ""
    author: str = ""
    page_count: int = 0
    # Parallel to paragraphs_text, or empty (legacy / no formatting)
    paragraphs_format: List[ParagraphFormat] = field(default_factory=list)
    boxes: List[PdfBox] = field(default_factory=list)
    tables: List[PdfTable] = field(default_factory=list)
    default_font_family: str = "serif"   # one of FONT_FAMILIES

    def format_at(self, index: int) -> ParagraphFormat:
        """Return the format of paragraph `index`, or a plain ParagraphFormat when none is recorded."""
        if self.paragraphs_format:
            return self.paragraphs_format[index]
        return ParagraphFormat()


class PdfError(Exception):
    """Base class for PDF processing errors."""


class PdfOpenError(PdfError):
    """File is not a readable PDF."""


class PdfEncryptedError(PdfError):
    """PDF requires a password."""


class PdfNoTextLayerError(PdfError):
    """PDF has no extractable text (scanned document)."""


class PdfBuildError(PdfError):
    """Output PDF could not be generated."""
