"""Tests for the PDF builder's paragraph formats, background boxes and tables."""

import os

import pymupdf
import pytest

from src.core.pdf.builder import _build_html, _text_element, build_pdf
from src.core.pdf.content import ParagraphFormat, PdfBox, PdfImage, PdfPlainContent, PdfTable

PAGE_W, PAGE_H = 595.0, 842.0

BOX_BG = "#f3f6f5"
BOX_BAR = "#9a5b2d"


def _content(texts, styles=None, formats=None, **kwargs):
    """Build a PdfPlainContent by hand (A4 pages)."""
    kwargs.setdefault("page_size", (PAGE_W, PAGE_H))
    return PdfPlainContent(
        paragraphs_text=list(texts),
        paragraphs_style=list(styles) if styles else ["normal"] * len(texts),
        paragraphs_format=list(formats) if formats else [],
        **kwargs,
    )


def _png(width=40, height=20):
    """Create a small solid-colour PNG without external imaging libraries."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix.tobytes("png")


def _build(tmp_path, translated, content, target="French", **kwargs):
    out = str(tmp_path / "out.pdf")
    build_pdf(translated, content, out, target, **kwargs)
    return out


def _spans(path):
    """Return every text span of the first page, keyed by its stripped text."""
    with pymupdf.open(path) as doc:
        blocks = doc[0].get_text("dict")["blocks"]
    spans = {}
    for block in blocks:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    spans.setdefault(span["text"].strip(), span)
    return spans


def _fills(path):
    """Return (rgb 0..255, rect) for every filled drawing of the first page."""
    with pymupdf.open(path) as doc:
        drawings = doc[0].get_drawings()
    return [
        (tuple(round(c * 255) for c in d["fill"]), pymupdf.Rect(d["rect"]))
        for d in drawings
        if d.get("fill") is not None
    ]


def _hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[k:k + 2], 16) for k in (0, 2, 4))


def _int_rgb(value):
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _same_color(rgb, hex_color):
    """Per-channel comparison with a +/-1 tolerance (PDF colours are stored as floats)."""
    return all(abs(a - b) <= 1 for a, b in zip(rgb, _hex_rgb(hex_color)))


def _fills_of(path, hex_color):
    return [rect for rgb, rect in _fills(path) if _same_color(rgb, hex_color)]


def _span_rect(span):
    return pymupdf.Rect(span["bbox"])


# --- Element style -----------------------------------------------------------


def test_text_element_style_parts_in_fixed_order():
    fmt = ParagraphFormat(color="#00897c", font_family="monospace", bold=True, italic=True)
    element = _text_element("x", "normal", True, False, fmt, "serif")
    assert element == (
        '<p dir="rtl" style="color:#00897c; font-family:monospace; '
        'font-weight:bold; font-style:italic">x</p>'
    )


def test_text_element_omits_inherited_family_and_heading_bold():
    heading = _text_element(
        "x", "heading2", False, False, ParagraphFormat(font_family="sans-serif", bold=True), "serif")
    assert heading == "<h2>x</h2>"
    body = _text_element("x", "normal", False, False, ParagraphFormat(font_family="serif"), "serif")
    assert body == "<p>x</p>"
    no_format = _text_element("x", "list", False, False)
    assert no_format == '<p class="list">x</p>'


def test_text_element_source_keeps_grey_and_cell_is_plain_p():
    fmt = ParagraphFormat(color="#00897c", italic=True)
    assert _text_element("x", "normal", False, True, fmt, "serif") == (
        '<p class="source" style="font-style:italic">x</p>'
    )
    assert _text_element("x", "cell", False, False, ParagraphFormat(), "serif") == "<p>x</p>"


def test_text_element_rejects_unsafe_style_values():
    fmt = ParagraphFormat(color='#000000" onload="x', font_family="fantasy; color:red")
    assert _text_element("<b>", "normal", False, False, fmt, "serif") == "<p>&lt;b&gt;</p>"


# --- Paragraph formats (criteria 1-3) -----------------------------------------


def test_color_and_monospace_family(tmp_path):
    fmt = ParagraphFormat(color="#00897c", font_family="monospace")
    out = _build(tmp_path, ["Eyebrow text"], _content(["src"], formats=[fmt]))

    span = _spans(out)["Eyebrow text"]
    assert _same_color(_int_rgb(span["color"]), "#00897c")
    assert "Mono" in span["font"]


@pytest.mark.parametrize("family, has_sans", [("sans-serif", True), ("serif", False)])
def test_default_font_family_sets_body_font(tmp_path, family, has_sans):
    out = _build(tmp_path, ["Plain body"], _content(["src"], default_font_family=family))
    assert ("Sans" in _spans(out)["Plain body"]["font"]) is has_sans


def test_unknown_default_font_family_falls_back_to_serif(tmp_path):
    out = _build(tmp_path, ["Plain body"], _content(["src"], default_font_family="cursive"))
    font = _spans(out)["Plain body"]["font"]
    assert "Sans" not in font and "Mono" not in font


def test_bold_and_italic_paragraphs(tmp_path):
    formats = [ParagraphFormat(bold=True), ParagraphFormat(italic=True)]
    out = _build(tmp_path, ["Bold body", "Italic body"], _content(["a", "b"], formats=formats))

    spans = _spans(out)
    assert "Bold" in spans["Bold body"]["font"]
    assert "Italic" in spans["Italic body"]["font"]


# --- Boxes (criterion 4) ------------------------------------------------------


def test_consecutive_boxed_paragraphs_share_one_box_with_left_bar(tmp_path):
    box = ParagraphFormat(box=0)
    content = _content(
        ["a", "b", "c", "d"],
        formats=[ParagraphFormat(), box, box, ParagraphFormat()],
        boxes=[PdfBox(BOX_BG, BOX_BAR)],
    )
    out = _build(tmp_path, ["Before box", "Inside one", "Inside two", "After box"], content)

    backgrounds = _fills_of(out, BOX_BG)
    bars = _fills_of(out, BOX_BAR)
    assert len(backgrounds) == 1
    assert len(bars) == 1
    background, bar = backgrounds[0], bars[0]

    spans = _spans(out)
    for text in ("Inside one", "Inside two"):
        assert background.contains(_span_rect(spans[text]))
    for text in ("Before box", "After box"):
        assert not background.intersects(_span_rect(spans[text]))

    assert abs(bar.x1 - background.x0) <= 1
    assert abs(bar.y0 - background.y0) <= 1
    assert abs(bar.y1 - background.y1) <= 1


def test_box_without_border_left_has_no_bar(tmp_path):
    content = _content(["a"], formats=[ParagraphFormat(box=0)], boxes=[PdfBox(BOX_BG)])
    out = _build(tmp_path, ["Boxed"], content)

    assert len(_fills_of(out, BOX_BG)) == 1
    assert len(_fills(out)) == 1


def test_box_html_groups_paragraphs_images_and_bilingual_sources():
    image = PdfImage(data=_png(), ext="png", width_pt=40.0, height_pt=20.0)
    box = ParagraphFormat(box=0)
    content = _content(
        ["s0", "s1", "s2", "s3"],
        formats=[box, box, ParagraphFormat(), box],
        boxes=[PdfBox(BOX_BG, BOX_BAR)],
        images_by_paragraph={0: [image]},
    )
    body, _ = _build_html(["t0", "t1", "t2", "t3"], content, 400, 600, "French", "English", True)

    div = f'<div class="box" style="background-color:{BOX_BG}; border-left:2pt solid {BOX_BAR}">'
    assert body.split("\n") == [
        div,
        '<p class="source">s0</p>', "<p>t0</p>",
        '<p class="img"><img src="img_0_0.png" style="width:40.0pt;height:20.0pt"/></p>',
        '<p class="source">s1</p>', "<p>t1</p>",
        "</div>",
        '<p class="source">s2</p>', "<p>t2</p>",
        div,
        '<p class="source">s3</p>', "<p>t3</p>",
        "</div>",
    ]


# --- Tables (criteria 5-7) -----------------------------------------------------


def _table_content(cells, rows, header=True, widths=(0.11, 0.53, 0.36), before="Intro", after=None):
    """A body paragraph, then a table whose non-empty cells are `cells`, then optional text."""
    texts = [before] + list(cells) + ([after] if after else [])
    styles = ["normal"] + ["cell"] * len(cells) + (["normal"] if after else [])
    formats = [ParagraphFormat() for _ in texts]
    header_count = sum(1 for index in rows[0] if index is not None) if header else 0
    for k in range(header_count):
        formats[1 + k] = ParagraphFormat(box=0)
    table = PdfTable(first_index=1, rows=rows, header=header, column_widths=list(widths))
    return texts, _content(texts, styles, formats, boxes=[PdfBox(BOX_BG)], tables=[table])


def test_table_cells_are_laid_out_in_a_grid(tmp_path):
    cells = ["ARTICLE", "OBLIGATION", "APPLICATION", "L1", "Ob1", "Ap1", "L2", "Ob2", "Ap2"]
    rows = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    texts, content = _table_content(cells, rows)
    out = _build(tmp_path, texts, content)

    spans = _spans(out)
    for text in cells:
        assert text in spans
    header_y = {round(spans[text]["bbox"][1]) for text in cells[:3]}
    assert max(header_y) - min(header_y) <= 1

    # Text column of the body: the table must fit inside it
    text_left = spans["Intro"]["bbox"][0]
    text_width = PAGE_W - 2 * text_left
    column_x = [spans[text]["bbox"][0] for text in cells[:3]]
    assert len({round(x) for x in column_x}) == 3
    for x, start in zip(column_x, (0.0, 0.11, 0.64)):
        assert x == pytest.approx(text_left + start * text_width, abs=8)

    header_fills = _fills_of(out, BOX_BG)
    assert header_fills
    assert max(rect.x1 for rect in header_fills) <= PAGE_W - text_left + 1
    for text in cells[:3]:
        assert any(rect.contains(_span_rect(spans[text])) for rect in header_fills)
    for text in cells[3:]:
        assert not any(rect.intersects(_span_rect(spans[text])) for rect in header_fills)

    # Data rows below the header, row-major reading order
    assert spans["L1"]["bbox"][1] > spans["ARTICLE"]["bbox"][1]
    assert spans["L2"]["bbox"][1] > spans["L1"]["bbox"][1]


def test_empty_cell_renders_nothing_and_keeps_columns(tmp_path):
    cells = ["H0", "H1", "H2", "A0", "A2", "B0", "B1", "B2"]
    rows = [[1, 2, 3], [4, None, 5], [6, 7, 8]]
    texts, content = _table_content(cells, rows, after="Closing paragraph")
    out = _build(tmp_path, texts, content)

    spans = _spans(out)
    assert set(cells) | {"Intro", "Closing paragraph"} == set(spans)
    assert spans["A2"]["bbox"][0] == pytest.approx(spans["H2"]["bbox"][0], abs=1)
    assert spans["A2"]["bbox"][1] == pytest.approx(spans["A0"]["bbox"][1], abs=1)
    assert spans["Closing paragraph"]["bbox"][1] > spans["B0"]["bbox"][1]


def test_images_anchored_in_a_table_follow_the_table(tmp_path):
    cells = ["H0", "H1", "A0", "A1"]
    rows = [[1, 2], [3, 4]]
    texts, content = _table_content(cells, rows, widths=(0.5, 0.5), after="Closing paragraph")
    image = PdfImage(data=_png(), ext="png", width_pt=60.0, height_pt=30.0)
    content.images_by_paragraph = {2: [image]}
    out = _build(tmp_path, texts, content)

    with pymupdf.open(out) as doc:
        image_blocks = [b for b in doc[0].get_text("dict")["blocks"] if b["type"] == 1]
    assert len(image_blocks) == 1
    spans = _spans(out)
    image_y = image_blocks[0]["bbox"][1]
    assert image_y > max(spans[text]["bbox"][3] for text in cells) - 1
    assert spans["Closing paragraph"]["bbox"][1] > image_y


def test_bilingual_table_cell_shows_source_before_translation(tmp_path):
    cells = ["Article", "Rule", "Code", "Value"]
    rows = [[1, 2], [3, 4]]
    texts, content = _table_content(cells, rows, widths=(0.4, 0.6))
    translated = ["Intro FR"] + [f"FR {text}" for text in cells]
    out = _build(tmp_path, translated, content, source_language="English", bilingual=True)

    spans = _spans(out)
    for text in cells:
        source, target = spans[text], spans[f"FR {text}"]
        assert source["bbox"][0] == pytest.approx(target["bbox"][0], abs=1)
        assert source["bbox"][1] < target["bbox"][1]
        assert source["color"] == 0x666666


def test_rtl_table_cell_is_right_aligned(tmp_path):
    hebrew = "שלום"
    cells = ["H0", "H1", "C0", "C1"]
    rows = [[1, 2], [3, 4]]
    texts, content = _table_content(cells, rows, widths=(0.5, 0.5))
    translated = ["Intro", "A", "B", "C", hebrew]
    out = _build(tmp_path, translated, content, target="Hebrew")

    with pymupdf.open(out) as doc:
        assert hebrew in doc[0].get_text()
    spans = _spans(out)
    assert hebrew in spans

    # The header cell fill of the same column gives the cell's horizontal extent
    column = max(_fills_of(out, BOX_BG), key=lambda rect: rect.x0)
    assert spans[hebrew]["bbox"][0] > column.x0 + 0.3 * column.width


# --- Validation (criterion 8) ----------------------------------------------------


def _valid_table_content():
    texts = ["p", "c1", "c2", "c3", "c4"]
    table = PdfTable(first_index=1, rows=[[1, 2], [3, 4]], column_widths=[0.5, 0.5])
    return texts, ["normal", "cell", "cell", "cell", "cell"], table


def _invalid_contents():
    texts, styles, table = _valid_table_content()

    def with_table(**changes):
        fields = {"first_index": table.first_index, "rows": table.rows,
                  "column_widths": table.column_widths}
        fields.update(changes)
        return _content(texts, styles, tables=[PdfTable(**fields)])

    return {
        "format_length": _content(texts, styles, formats=[ParagraphFormat()] * 4),
        "box_out_of_range": _content(
            texts, styles, formats=[ParagraphFormat(box=1)] + [ParagraphFormat()] * 4,
            boxes=[PdfBox(BOX_BG)]),
        "table_index_out_of_range": with_table(first_index=3, rows=[[3, 4], [5, None]]),
        "non_contiguous": with_table(rows=[[1, 2], [4, None]]),
        "not_row_major": with_table(rows=[[1, 3], [2, 4]]),
        "wrong_first_index": with_table(first_index=0),
        "empty_table": with_table(rows=[[None, None]]),
        "column_width_mismatch": with_table(column_widths=[1.0]),
        "overlapping_tables": _content(texts, styles, tables=[
            table, PdfTable(first_index=2, rows=[[2]], column_widths=[1.0])]),
    }


@pytest.mark.parametrize("case", sorted(_invalid_contents()))
def test_invalid_content_raises_value_error_before_rendering(tmp_path, case):
    content = _invalid_contents()[case]
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError):
        build_pdf(list(content.paragraphs_text), content, str(out), "French")
    assert not os.path.exists(out)


def test_valid_table_content_builds(tmp_path):
    texts, styles, table = _valid_table_content()
    out = _build(tmp_path, texts, _content(texts, styles, tables=[table]))
    spans = _spans(out)
    assert {"p", "c1", "c2", "c3", "c4"} <= set(spans)


def test_content_is_not_mutated(tmp_path):
    cells = ["H0", "H1", "A0", "A1"]
    texts, content = _table_content(cells, [[1, 2], [3, 4]], widths=(0.5, 0.5))
    snapshot = repr(content)
    _build(tmp_path, texts, content, source_language="English", bilingual=True)
    assert repr(content) == snapshot
