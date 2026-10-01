"""Tests for src.core.pdf.layout (ruled tables and background boxes)."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from src.core.pdf.layout import DetectedBox, DetectedTable, detect_boxes, detect_tables  # noqa: E402


def _line_bboxes(page):
    return [
        tuple(line["bbox"])
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
        if "".join(span["text"] for span in line["spans"]).strip()
    ]


def _tables(page):
    return detect_tables(page, _line_bboxes(page))


def _hex_close(actual, expected, tolerance=1):
    assert actual is not None
    a = [int(actual[i:i + 2], 16) for i in (1, 3, 5)]
    e = [int(expected[i:i + 2], 16) for i in (1, 3, 5)]
    assert all(abs(x - y) <= tolerance for x, y in zip(a, e)), (actual, expected)


def _assert_contract(table: DetectedTable):
    assert [len(row) for row in table.cells] == [len(row) for row in table.cell_bboxes]
    assert len(table.column_widths) == len(table.cells[0])
    assert sum(table.column_widths) == pytest.approx(1.0, abs=1e-6)


@pytest.fixture
def formatted_doc(formatted_pdf_path):
    doc = pymupdf.open(formatted_pdf_path)
    yield doc
    doc.close()


def test_title_block_is_one_block(formatted_doc):
    blocks = [b for b in formatted_doc[0].get_text("dict")["blocks"] if b.get("type") == 0]
    texts = ["".join(s["text"] for s in line["spans"]) for line in blocks[0]["lines"]]
    assert texts == [
        "ACME - INTERNAL NOTE",
        "Legal framework of the review",
        "Texts that apply to the weekly review tool.",
    ]


def test_chrome_style_table_with_header(formatted_doc):
    tables = _tables(formatted_doc[0])
    assert len(tables) == 1
    table = tables[0]
    _assert_contract(table)
    assert table.header is True
    _hex_close(table.header_fill, "#f3f6f5")
    assert len(table.cells) == 3
    assert all(len(row) == 3 for row in table.cells)
    assert table.cells[0] == ["ARTICLE", "OBLIGATION", "APPLICATION"]
    assert table.cells[1][0] == "L1222-3"
    empty = [cell for row in table.cells for cell in row if cell in (None, "")]
    assert len(empty) == 1
    expected = [55 / 504, 265 / 504, 184 / 504]
    assert table.column_widths == pytest.approx(expected, abs=0.01)


def test_continuation_table_without_header(formatted_doc):
    tables = _tables(formatted_doc[1])
    assert len(tables) == 1
    table = tables[0]
    _assert_contract(table)
    assert table.header is False
    assert table.header_fill is None
    assert table.cells[0] == ["L1222-5", "Continued obligation text.", "Applies to every team."]
    assert table.cells[1][0] == "L1222-6"
    assert table.bbox[1] < 120


def test_fully_ruled_table(ruled_table_pdf_path):
    doc = pymupdf.open(ruled_table_pdf_path)
    try:
        # The table comes from PyMuPDF's own detector (step 1): the strokes are
        # not filled rule segments, so no rule grid exists on this page.
        found = [(t.row_count, t.col_count) for t in doc[0].find_tables().tables]
        tables = _tables(doc[0])
    finally:
        doc.close()
    assert found == [(3, 3)]
    assert len(tables) == 1
    table = tables[0]
    _assert_contract(table)
    assert table.header is False
    assert table.cells == [["Fruit", "Colour", "Taste"], ["Apple", "Red", "Sweet"], ["Lemon", "Yellow", "Sour"]]
    assert table.column_widths == pytest.approx([1 / 3] * 3, abs=1e-3)


def test_callout_box_with_left_bar(formatted_doc):
    page = formatted_doc[0]
    tables = _tables(page)
    boxes = detect_boxes(page, [t.bbox for t in tables])
    assert len(boxes) == 1
    box = boxes[0]
    assert box.rect == pytest.approx((46, 250, 550, 320), abs=0.1)
    _hex_close(box.background, "#f3f6f5")
    _hex_close(box.border_left, "#9a5b2d")


def test_header_fills_are_boxes_without_exclusion(formatted_doc):
    # Without the table exclusion the header cell fills are boxes too, but never
    # the white page background or the thin rules.
    boxes = detect_boxes(formatted_doc[0], [])
    assert len(boxes) == 4
    assert all(b.rect[3] - b.rect[1] > 2.0 for b in boxes)
    assert all(b.background != "#ffffff" for b in boxes)
    areas = [(b.rect[2] - b.rect[0]) * (b.rect[3] - b.rect[1]) for b in boxes]
    assert areas == sorted(areas)


def test_no_box_on_continuation_page(formatted_doc):
    page = formatted_doc[1]
    assert detect_boxes(page, [t.bbox for t in _tables(page)]) == []


def test_simple_pdf_has_no_layout(simple_pdf_path):
    doc = pymupdf.open(simple_pdf_path)
    try:
        for page in doc:
            assert _tables(page) == []
            assert detect_boxes(page, []) == []
    finally:
        doc.close()


def test_detection_is_deterministic(formatted_doc):
    for page in formatted_doc:
        assert _tables(page) == _tables(page)
        assert detect_boxes(page, []) == detect_boxes(page, [])


class _RaisingPage:
    rect = pymupdf.Rect(0, 0, 595, 842)

    def find_tables(self, *args, **kwargs):
        raise RuntimeError("broken table finder")

    def get_drawings(self, *args, **kwargs):
        raise RuntimeError("broken drawings")


def test_failures_yield_empty_lists():
    page = _RaisingPage()
    assert detect_tables(page, [(0, 0, 10, 10)]) == []
    assert detect_boxes(page, []) == []


class _StubPage:
    """A page with malformed drawings and no PyMuPDF tables."""
    rect = pymupdf.Rect(0, 0, 595, 842)

    def __init__(self, drawings):
        self._drawings = drawings

    def find_tables(self, *args, **kwargs):
        return type("Finder", (), {"tables": []})()

    def get_drawings(self, *args, **kwargs):
        return self._drawings


def test_malformed_drawings_are_tolerated():
    drawings = [
        "not a dict",
        {"items": None, "fill": (0.5, 0.5, 0.5)},
        {"fill": None, "color": None, "items": [("re", pymupdf.Rect(10, 10, 100, 100))]},
        {"fill": (0.2, 0.4, 0.6, 0.8), "items": [("re", pymupdf.Rect(10, 10, 100, 100))]},
        {"fill": (0.2, 0.4, 0.6), "items": [("qu", pymupdf.Rect(0, 0, 50, 50).quad), ("re",), "x"]},
        {"fill": (0.2, 0.4, 0.6), "fill_opacity": None, "items": [("re", pymupdf.Rect(100, 100, 200, 160))]},
        {"fill": (0.2, 0.4, 0.6), "fill_opacity": 0.2, "items": [("re", pymupdf.Rect(300, 300, 400, 400))]},
        {"color": (0, 0, 0), "fill": None, "items": [("l", pymupdf.Point(0, 0)), ("l", None, None)]},
    ]
    page = _StubPage(drawings)
    assert detect_tables(page, [None, (1, 2)]) == []
    assert detect_boxes(page, [None]) == [DetectedBox((100.0, 100.0, 200.0, 160.0), "#336699", None)]
