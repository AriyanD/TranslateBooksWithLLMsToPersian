"""
PDF builder: translated paragraphs -> reflowed PDF.

The output is a new document laid out with PyMuPDF's Story/DocumentWriter
(HTML + CSS -> PDF). The original page layout is not preserved: headings,
paragraphs, list items, tables and images are re-flowed on pages of the original
size. Paragraph formats (colour, font family, bold, italic) and background boxes
recorded by the extractor are reproduced with inline CSS.
"""

import html
import io
import re
import string
from typing import List, Optional, Tuple

import pymupdf

from src.core.epub.rtl_support import is_rtl_language
from src.core.pdf.content import (
    FONT_FAMILIES,
    ParagraphFormat,
    PdfBuildError,
    PdfPlainContent,
    PdfTable,
)

_MAX_MARGIN_PT = 72.0
_MIN_MARGIN_PT = 36.0
_MARGIN_RATIO = 0.1

_PAGES_PER_ITEM = 10
_PAGES_SLACK = 10

# MuPDF's default stylesheet gives <body> a 1em margin (11pt at the body font size),
# so the text column is narrower than the story rectangle by twice this value.
_BODY_MARGIN_PT = 11.0
# Horizontal padding of a table cell (left + right), see "table.grid td" below
_CELL_PADDING_PT = 8.0
_MIN_CELL_WIDTH_PT = 10.0

_HEADING_TAGS = {"heading1": "h1", "heading2": "h2", "heading3": "h3"}
# Font family the heading tags inherit from the stylesheet
_HEADING_FAMILY = "sans-serif"
_DEFAULT_FAMILY = "serif"

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Story stylesheet; ${family} is the document's default font family
_PDF_CSS = string.Template("""
body { font-family: ${family}; font-size: 11pt; line-height: 1.4; }
p { margin: 0 0 6pt 0; text-align: justify; }
h1, h2, h3 { font-family: sans-serif; font-weight: bold; margin: 12pt 0 6pt 0; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
p.list { margin-left: 18pt; text-align: left; }
.source { color: #666666; }
p.img { text-align: center; margin: 6pt 0; }
table.grid { width: 100%; border-collapse: collapse; margin: 6pt 0; }
table.grid td, table.grid th { padding: 3pt 4pt; vertical-align: top; text-align: left; border-bottom: 0.5pt solid #cccccc; }
table.grid th { font-weight: bold; }
table.grid p { margin: 0; text-align: left; }
div.box { padding: 4pt 6pt; margin: 6pt 0; }
""")


def build_pdf(
    translated_paragraphs: List[str],
    content: PdfPlainContent,
    output_path: str,
    target_language: str,
    source_language: str = "",
    bilingual: bool = False,
) -> None:
    """
    Write a reflowed PDF made of the translated paragraphs.

    Args:
        translated_paragraphs: Translated texts, parallel to content.paragraphs_text.
        content: Extracted structure (styles, formats, boxes, tables, images,
            page size, metadata).
        output_path: Destination PDF path.
        target_language: Target language (name or code), used for RTL detection.
        source_language: Source language, used for RTL detection of source text
            in bilingual mode.
        bilingual: When True, each source paragraph (or table cell) is emitted
            right before its translation.

    Raises:
        ValueError: If the paragraph count does not match the extracted content,
            or if the content's formats, boxes or tables are inconsistent.
        PdfBuildError: If PyMuPDF fails to render or save the document, or if the
            layout does not converge.
    """
    if len(translated_paragraphs) != len(content.paragraphs_text):
        raise ValueError(
            f"Translated paragraph count ({len(translated_paragraphs)}) does not match "
            f"source paragraph count ({len(content.paragraphs_text)})"
        )
    _validate(content)

    width, height = content.page_size or pymupdf.paper_size("a4")
    margin = min(_MAX_MARGIN_PT, max(_MIN_MARGIN_PT, _MARGIN_RATIO * min(width, height)))
    mediabox = pymupdf.Rect(0, 0, width, height)
    where = mediabox + (margin, margin, -margin, -margin)

    total_images = sum(len(images) for images in content.images_by_paragraph.values())
    max_pages = _PAGES_PER_ITEM * (len(translated_paragraphs) + total_images) + _PAGES_SLACK

    try:
        body_html, archive = _build_html(
            translated_paragraphs, content, where.width, where.height,
            target_language, source_language, bilingual,
        )
        css = _PDF_CSS.substitute(family=_document_family(content))
        buffer = _render_story(body_html, archive, mediabox, where, max_pages, css)
        _save_with_metadata(buffer, content, output_path)
    except PdfBuildError:
        raise
    except Exception as exc:
        raise PdfBuildError(str(exc)) from exc


def _validate(content: PdfPlainContent) -> None:
    """Check the format, box and table invariants of the content (raises ValueError)."""
    count = len(content.paragraphs_text)
    if content.paragraphs_format and len(content.paragraphs_format) != count:
        raise ValueError(
            f"Paragraph format count ({len(content.paragraphs_format)}) does not match "
            f"paragraph count ({count})"
        )
    for i, fmt in enumerate(content.paragraphs_format):
        if fmt.box is not None and not 0 <= fmt.box < len(content.boxes):
            raise ValueError(f"Paragraph {i} references box {fmt.box} out of {len(content.boxes)}")

    next_free = 0
    for t, table in enumerate(content.tables):
        indices = [index for row in table.rows for index in row if index is not None]
        for index in indices:
            if not 0 <= index < count:
                raise ValueError(f"Table {t} references paragraph {index} out of {count}")
        if not indices or indices != list(range(table.first_index, table.first_index + len(indices))):
            raise ValueError(
                f"Table {t} cells are not the contiguous row-major range "
                f"starting at {table.first_index}"
            )
        if table.first_index < next_free:
            raise ValueError(f"Table {t} overlaps or precedes the previous table")
        n_cols = len(table.rows[0])
        if any(len(row) != n_cols for row in table.rows) or len(table.column_widths) != n_cols:
            raise ValueError(f"Table {t} rows and column widths do not have the same length")
        next_free = indices[-1] + 1


def _document_family(content: PdfPlainContent) -> str:
    """Return the body font family, falling back to serif for unknown values."""
    family = content.default_font_family
    return family if family in FONT_FAMILIES else _DEFAULT_FAMILY


def _safe_color(value: Optional[str]) -> Optional[str]:
    """Return value when it is a "#rrggbb" colour, else None (the model comes from untrusted PDFs)."""
    return value if value and _HEX_COLOR_RE.match(value) else None


def _style_attr(parts: List[str]) -> str:
    """Render an inline style attribute, or nothing when there is no part."""
    if not parts:
        return ""
    return f' style="{html.escape("; ".join(parts), quote=True)}"'


def _text_element(
    text: str,
    style: str,
    rtl: bool,
    source: bool,
    fmt: Optional[ParagraphFormat] = None,
    default_family: str = _DEFAULT_FAMILY,
) -> str:
    """Render one paragraph as an HTML element for the given style and format."""
    fmt = fmt or ParagraphFormat()
    if style in _HEADING_TAGS:
        tag = _HEADING_TAGS[style]
        classes = ["source"] if source else []
        inherited_family = _HEADING_FAMILY
    else:
        tag = "p"
        classes = (["list"] if style == "list" else []) + (["source"] if source else [])
        inherited_family = default_family

    style_parts: List[str] = []
    color = _safe_color(fmt.color)
    if color and not source:
        style_parts.append(f"color:{color}")
    if fmt.font_family in FONT_FAMILIES and fmt.font_family != inherited_family:
        style_parts.append(f"font-family:{fmt.font_family}")
    if fmt.bold and tag == "p":
        style_parts.append("font-weight:bold")
    if fmt.italic:
        style_parts.append("font-style:italic")

    class_attr = f' class="{" ".join(classes)}"' if classes else ""
    dir_attr = ' dir="rtl"' if rtl else ""
    style_attr = _style_attr(style_parts)
    return f"<{tag}{class_attr}{dir_attr}{style_attr}>{html.escape(text, quote=False)}</{tag}>"


def _build_html(
    translated_paragraphs: List[str],
    content: PdfPlainContent,
    content_width: float,
    content_height: float,
    target_language: str,
    source_language: str,
    bilingual: bool,
) -> Tuple[str, "pymupdf.Archive"]:
    """Build the story HTML and the archive holding its images."""
    writer = _HtmlWriter(
        translated_paragraphs, content, content_width, content_height,
        is_rtl_language(target_language), is_rtl_language(source_language), bilingual,
    )
    return writer.build()


class _HtmlWriter:
    """Accumulates the story HTML: paragraphs, background boxes, tables and images."""

    def __init__(
        self,
        translated_paragraphs: List[str],
        content: PdfPlainContent,
        content_width: float,
        content_height: float,
        target_rtl: bool,
        source_rtl: bool,
        bilingual: bool,
    ) -> None:
        self.translated = translated_paragraphs
        self.content = content
        self.content_width = content_width
        self.content_height = content_height
        self.target_rtl = target_rtl
        self.source_rtl = source_rtl
        self.bilingual = bilingual
        self.default_family = _document_family(content)
        self.tables_by_start = {table.first_index: table for table in content.tables}
        self.archive = pymupdf.Archive()
        self.parts: List[str] = []
        self.open_box: Optional[int] = None

    def build(self) -> Tuple[str, "pymupdf.Archive"]:
        """Walk the paragraphs in order and return (html, archive)."""
        self._add_images(-1)
        i = 0
        count = len(self.content.paragraphs_text)
        while i < count:
            table = self.tables_by_start.get(i)
            if table is not None:
                i = self._add_table(table)
            else:
                self._add_paragraph(i)
                i += 1
        self._set_box(None)
        return "\n".join(self.parts), self.archive

    def _style(self, index: int) -> str:
        styles = self.content.paragraphs_style
        return styles[index] if index < len(styles) else "normal"

    def _add_images(self, index: int) -> None:
        """Emit the images anchored after paragraph `index` (-1 = before the first one)."""
        key = "m1" if index == -1 else str(index)
        for k, image in enumerate(self.content.images_by_paragraph.get(index, [])):
            name = f"img_{key}_{k}.{image.ext}"
            self.archive.add(image.data, name)
            scale = min(
                1.0,
                self.content_width / image.width_pt,
                0.9 * self.content_height / image.height_pt,
            )
            self.parts.append(
                f'<p class="img"><img src="{name}" '
                f'style="width:{image.width_pt * scale:.1f}pt;'
                f'height:{image.height_pt * scale:.1f}pt"/></p>'
            )

    def _set_box(self, box: Optional[int]) -> None:
        """Close the open background box unless it is `box`, then open `box` if needed."""
        if box == self.open_box:
            return
        if self.open_box is not None:
            self.parts.append("</div>")
        self.open_box = box
        if box is not None:
            pdf_box = self.content.boxes[box]
            style_parts = []
            background = _safe_color(pdf_box.background)
            if background:
                style_parts.append(f"background-color:{background}")
            border_left = _safe_color(pdf_box.border_left)
            if border_left:
                style_parts.append(f"border-left:2pt solid {border_left}")
            self.parts.append(f'<div class="box"{_style_attr(style_parts)}>')

    def _texts(self, index: int, style: str) -> List[str]:
        """Render the (bilingual source and) translated elements of one paragraph."""
        fmt = self.content.format_at(index)
        source_text = self.content.paragraphs_text[index]
        translated = self.translated[index] or ""
        elements = []
        if self.bilingual and source_text.strip():
            elements.append(_text_element(
                source_text, style, self.source_rtl, True, fmt, self.default_family))
        if translated.strip():
            elements.append(_text_element(
                translated, style, self.target_rtl, False, fmt, self.default_family))
        return elements

    def _add_paragraph(self, index: int) -> None:
        """Emit one paragraph outside tables, inside its background box if any."""
        self._set_box(self.content.format_at(index).box)
        self.parts.extend(self._texts(index, self._style(index)))
        self._add_images(index)

    def _add_table(self, table: PdfTable) -> int:
        """Emit a table and the images anchored to its cells; return the next paragraph index."""
        self._set_box(None)
        table_width = self.content_width - 2 * _BODY_MARGIN_PT
        self.parts.append('<table class="grid">')
        for r, row in enumerate(table.rows):
            self.parts.append("<tr>")
            for c, index in enumerate(row):
                width = max(_MIN_CELL_WIDTH_PT, table.column_widths[c] * table_width - _CELL_PADDING_PT)
                self.parts.append(self._cell(index, width, table.header and r == 0))
            self.parts.append("</tr>")
        self.parts.append("</table>")

        last_index = max(index for row in table.rows for index in row if index is not None)
        for index in range(table.first_index, last_index + 1):
            self._add_images(index)
        return last_index + 1

    def _cell(self, index: Optional[int], width: float, header: bool) -> str:
        """Render one table cell (<th> for header cells, <td> otherwise)."""
        tag = "th" if header else "td"
        style_parts = [f"width:{width:.1f}pt"]
        inner = ""
        if index is not None:
            box = self.content.format_at(index).box
            if header and box is not None:
                background = _safe_color(self.content.boxes[box].background)
                if background:
                    style_parts.append(f"background-color:{background}")
            # Cell paragraphs are plain <p> elements, whatever their recorded style
            inner = "".join(self._texts(index, "cell"))
        return f"<{tag}{_style_attr(style_parts)}>{inner}</{tag}>"


def _render_story(
    body_html: str,
    archive: "pymupdf.Archive",
    mediabox: "pymupdf.Rect",
    where: "pymupdf.Rect",
    max_pages: int,
    css: str,
) -> io.BytesIO:
    """Lay the HTML out page by page and return the rendered PDF bytes."""
    # An empty story would produce no page at all; keep the output a valid PDF
    story = pymupdf.Story(html=body_html or "<p></p>", user_css=css, archive=archive)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)

    pages = 0
    more = True
    while more:
        if pages >= max_pages:
            raise PdfBuildError("PDF layout did not converge")
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1

    writer.close()
    return buffer


def _save_with_metadata(buffer: io.BytesIO, content: PdfPlainContent, output_path: str) -> None:
    """Stamp document metadata on the rendered PDF and save it."""
    # Attribution stamp, read at call time so the ATTRIBUTION_ENABLED switch is honoured
    import src.config as cfg
    producer = cfg.GENERATOR_NAME if cfg.ATTRIBUTION_ENABLED else ""

    doc = pymupdf.open("pdf", buffer.getvalue())
    try:
        doc.set_metadata({
            "title": content.title,
            "author": content.author,
            "producer": producer,
            "creator": producer,
        })
        doc.save(output_path, garbage=3, deflate=True)
    finally:
        doc.close()
