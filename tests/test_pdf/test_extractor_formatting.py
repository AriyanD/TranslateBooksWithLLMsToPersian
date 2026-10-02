"""
Tests for the extractor's formatting output: heading splits, table cells,
paragraph formats, background boxes and the document font family.

Fixtures are generated with PyMuPDF in tests/test_pdf/conftest.py.
"""

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

import src.core.pdf as pdf_package  # noqa: E402
from src.core.pdf import FONT_FAMILIES, PARAGRAPH_STYLES, PdfBox  # noqa: E402
from src.core.pdf import builder, extractor  # noqa: E402
from src.core.pdf.extractor import extract_pdf_paragraphs, extract_pdf_text  # noqa: E402

ALL_FIXTURES = [
    "simple_pdf_path",
    "multipage_pdf_path",
    "list_pdf_path",
    "image_pdf_path",
    "formatted_pdf_path",
    "ruled_table_pdf_path",
]


def _channels(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _assert_color(actual, expected):
    """Compare '#rrggbb' colours per channel with a tolerance of 1."""
    assert actual is not None, f"expected {expected}, got None"
    assert all(abs(a - e) <= 1 for a, e in zip(_channels(actual), _channels(expected))), (actual, expected)


def _index_of(content, text):
    matches = [i for i, paragraph in enumerate(content.paragraphs_text) if paragraph == text]
    assert len(matches) == 1, (text, content.paragraphs_text)
    return matches[0]


def _table_indices(table):
    return [index for row in table.rows for index in row if index is not None]


def _assert_invariants(content):
    """Every invariant of the extractor contract and of the formatting data model."""
    count = len(content.paragraphs_text)
    assert len(content.paragraphs_style) == count
    assert len(content.paragraphs_format) == count
    assert all(style in PARAGRAPH_STYLES for style in content.paragraphs_style)
    assert all(text and text == text.strip() and "\n" not in text for text in content.paragraphs_text)
    for key in content.images_by_paragraph:
        assert -1 <= key <= count - 1

    for fmt in content.paragraphs_format:
        assert fmt.box is None or 0 <= fmt.box < len(content.boxes)
        assert fmt.font_family is None or fmt.font_family in FONT_FAMILIES
    assert content.default_font_family in FONT_FAMILIES

    cell_indices = []
    next_free = 0
    for table in content.tables:
        indices = _table_indices(table)
        assert indices and indices == list(range(table.first_index, table.first_index + len(indices)))
        assert table.first_index >= next_free
        next_free = indices[-1] + 1
        assert all(content.paragraphs_style[index] == "cell" for index in indices)
        width = len(table.rows[0])
        assert all(len(row) == width for row in table.rows)
        assert len(table.column_widths) == width
        assert sum(table.column_widths) == pytest.approx(1.0, abs=1e-6)
        cell_indices.extend(indices)
    assert cell_indices == [i for i, style in enumerate(content.paragraphs_style) if style == "cell"]

    builder._validate(content)  # the builder's own invariant oracle


# --- 1. Title block split into eyebrow / title / subtitle ---------------------


def test_title_block_split_with_styles_and_formats(formatted_pdf_path):
    content = extract_pdf_paragraphs(formatted_pdf_path)

    eyebrow = _index_of(content, "ACME - INTERNAL NOTE")
    title = _index_of(content, "Legal framework of the review")
    subtitle = _index_of(content, "Texts that apply to the weekly review tool.")
    assert (eyebrow, title, subtitle) == (0, 1, 2)
    assert content.paragraphs_style[:3] == ["normal", "heading1", "normal"]

    eyebrow_format = content.format_at(eyebrow)
    _assert_color(eyebrow_format.color, "#00897c")
    assert eyebrow_format.font_family == "monospace"
    _assert_color(content.format_at(subtitle).color, "#56635f")
    assert content.format_at(title).color is None


# --- 2. Callout box ----------------------------------------------------------


def test_callout_paragraphs_share_one_box(formatted_pdf_path):
    content = extract_pdf_paragraphs(formatted_pdf_path)

    first = _index_of(content, "The first callout paragraph explains the rule.")
    second = _index_of(content, "The second callout paragraph gives an example.")
    box = content.format_at(first).box
    assert box is not None
    assert content.format_at(second).box == box
    background = content.boxes[box].background
    border = content.boxes[box].border_left
    _assert_color(background, "#f3f6f5")
    _assert_color(border, "#9a5b2d")
    assert content.boxes[box] == PdfBox(background, border)
    assert content.format_at(first - 1).box is None


# --- 3. Tables ---------------------------------------------------------------


def test_tables_become_cell_units(formatted_pdf_path):
    content = extract_pdf_paragraphs(formatted_pdf_path)

    assert len(content.tables) == 2
    first, continuation = content.tables
    assert first.header is True
    assert continuation.header is False
    for table in content.tables:
        indices = _table_indices(table)
        assert indices == list(range(table.first_index, table.first_index + len(indices)))
        assert all(content.paragraphs_style[index] == "cell" for index in indices)

    code = _index_of(content, "L1222-3")
    assert content.paragraphs_style[code] == "cell"
    assert content.format_at(code).font_family == "monospace"
    assert first.rows[1][0] == code
    assert first.rows[2][2] is None
    assert [content.paragraphs_text[i] for i in first.rows[0]] == ["ARTICLE", "OBLIGATION", "APPLICATION"]

    for index, style in enumerate(content.paragraphs_style):
        if style != "cell":
            assert "L1222-3" not in content.paragraphs_text[index]
            assert "ARTICLE" not in content.paragraphs_text[index]

    for index in first.rows[0]:
        box = content.format_at(index).box
        assert box is not None
        _assert_color(content.boxes[box].background, "#f3f6f5")
    assert all(content.format_at(index).box is None for row in first.rows[1:] for index in row if index is not None)
    assert first.column_widths == pytest.approx([55 / 504, 265 / 504, 184 / 504], abs=0.01)


# --- 4. Heading after a table ------------------------------------------------


def test_heading_follows_last_cell_of_first_table(formatted_pdf_path):
    content = extract_pdf_paragraphs(formatted_pdf_path)

    heading = _index_of(content, "2 Second section")
    assert content.paragraphs_style[heading].startswith("heading")
    assert heading == _table_indices(content.tables[0])[-1] + 1


# --- 5. Fully ruled table ----------------------------------------------------


def test_ruled_table_after_body_paragraph(ruled_table_pdf_path):
    content = extract_pdf_paragraphs(ruled_table_pdf_path)

    assert len(content.tables) == 1
    table = content.tables[0]
    assert len(table.rows) == 3 and all(len(row) == 3 for row in table.rows)
    assert content.paragraphs_text[0] == "The table below lists three fruits and their colours."
    assert content.paragraphs_style[0] == "normal"
    assert table.first_index == 1
    assert [content.paragraphs_text[i] for i in table.rows[1]] == ["Apple", "Red", "Sweet"]


# --- 6. Invariants on every fixture ------------------------------------------


@pytest.mark.parametrize("fixture_name", ALL_FIXTURES)
def test_invariants_on_every_fixture(fixture_name, request):
    path = request.getfixturevalue(fixture_name)
    _assert_invariants(extract_pdf_paragraphs(path))
    _assert_invariants(extract_pdf_paragraphs(path, detect_layout=False))


# --- 7. Determinism ----------------------------------------------------------


@pytest.mark.parametrize("fixture_name", ["formatted_pdf_path", "ruled_table_pdf_path"])
def test_extraction_is_deterministic_for_path_and_bytes(fixture_name, request):
    path = request.getfixturevalue(fixture_name)

    from_path = extract_pdf_paragraphs(path)
    again = extract_pdf_paragraphs(path)
    from_bytes = extract_pdf_paragraphs(Path(path).read_bytes())
    assert from_path == again == from_bytes
    assert from_path.paragraphs_format == from_bytes.paragraphs_format
    assert from_path.boxes == from_bytes.boxes
    assert from_path.tables == from_bytes.tables
    assert from_path.default_font_family == from_bytes.default_font_family


# --- 8. Layout detection disabled --------------------------------------------


def test_detect_layout_false_keeps_table_text_in_paragraphs(formatted_pdf_path):
    content = extract_pdf_paragraphs(formatted_pdf_path, detect_layout=False)

    assert content.tables == []
    assert content.boxes == []
    assert all(fmt.box is None for fmt in content.paragraphs_format)
    assert "cell" not in content.paragraphs_style
    assert any("L1222-3" in text for text in content.paragraphs_text)
    assert any("ARTICLE" in text for text in content.paragraphs_text)


def test_package_wrapper_passes_detect_layout(formatted_pdf_path):
    assert pdf_package.extract_pdf_paragraphs(formatted_pdf_path, detect_layout=False).tables == []
    assert len(pdf_package.extract_pdf_paragraphs(formatted_pdf_path).tables) == 2


def test_extract_pdf_text_skips_layout_detection(formatted_pdf_path):
    text = extract_pdf_text(formatted_pdf_path)
    assert "ARTICLE OBLIGATION APPLICATION" in text
    assert "\n\nLegal framework of the review\n\n" in text


# --- Formats, merge and split rules ------------------------------------------


def _save(doc, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


def test_legacy_document_gets_plain_formats_and_its_family(simple_pdf_path):
    content = extract_pdf_paragraphs(simple_pdf_path)

    assert content.boxes == [] and content.tables == []
    assert all(fmt.color is None and fmt.box is None for fmt in content.paragraphs_format)
    assert content.format_at(0).bold is True
    assert content.default_font_family == "sans-serif"


def test_paragraphs_with_different_formats_are_not_merged(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "A sentence that has no end", fontsize=11, fontname="helv")
    page.insert_text((72, 160), "continues in a red block.", fontsize=11, fontname="helv", color=(0.8, 0, 0))
    page.insert_text((72, 220), "Another sentence without end", fontsize=11, fontname="helv")
    page.insert_text((72, 280), "continues in a black block.", fontsize=11, fontname="helv")
    content = extract_pdf_paragraphs(_save(doc, tmp_path / "merge.pdf"))

    assert content.paragraphs_text == [
        "A sentence that has no end",
        "continues in a red block.",
        "Another sentence without end continues in a black block.",
    ]
    _assert_color(content.format_at(1).color, "#cc0000")
    _assert_invariants(content)


def test_block_split_on_uniform_colour_change(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Black first line of the block", fontsize=11, fontname="helv")
    page.insert_text((72, 114), "Blue second line of the block", fontsize=11, fontname="helv", color=(0, 0, 0.8))
    page.insert_text(
        (72, 200), "A long body paragraph sets the body size of this small test page.",
        fontsize=11, fontname="helv",
    )
    path = _save(doc, tmp_path / "colour_split.pdf")

    with pymupdf.open(path) as check:
        blocks = check[0].get_text("dict", flags=extractor.TEXT_FLAGS)["blocks"]
        assert len(blocks[0]["lines"]) == 2  # the precondition: one block, two lines

    content = extract_pdf_paragraphs(path)
    assert content.paragraphs_text[:2] == ["Black first line of the block", "Blue second line of the block"]
    assert content.format_at(0).color is None
    _assert_color(content.format_at(1).color, "#0000cc")


def test_light_text_without_box_loses_its_colour(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Nearly white text on a white page.", fontsize=11, fontname="helv",
                     color=(0.95, 0.95, 0.95))
    page.insert_text((72, 160), "Ordinary body text closes the page.", fontsize=11, fontname="helv")
    content = extract_pdf_paragraphs(_save(doc, tmp_path / "light.pdf"))

    assert content.format_at(0).color is None


def test_format_tolerates_spans_with_missing_keys():
    formatter = extractor._Formatter(extractor.FontResolver(None), {})
    fmt = formatter.paragraph(0, [{"spans": [{"text": "bare span"}]}], "normal")

    assert fmt.color is None and fmt.box is None and fmt.font_family is None
    assert fmt.bold is False and fmt.italic is False
    assert formatter.default_family() == "serif"
