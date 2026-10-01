"""
PDF fixtures generated with PyMuPDF at test time (no binary fixtures committed).

Every fixture returns the path of a freshly written PDF as a str.
"""

import io

import pytest

PAGE_WIDTH, PAGE_HEIGHT = 595, 842
BODY_SIZE = 11


def _pymupdf():
    # Imported lazily so that a missing PyMuPDF skips only the tests using these
    # fixtures, not every module of the directory.
    return pytest.importorskip("pymupdf")


def _new_page(doc):
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _png_bytes(width: int, height: int, color) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _save(doc, path, **kwargs) -> str:
    doc.save(str(path), **kwargs)
    doc.close()
    return str(path)


@pytest.fixture
def simple_pdf_path(tmp_path):
    """A 20pt bold title followed by two 11pt body paragraphs (three blocks)."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72, 80), "A Simple Title", fontsize=20, fontname="hebo")
    page.insert_text(
        (72, 130),
        "This is the first body paragraph of the document.\n"
        "It spans two lines of ordinary running text.",
        fontsize=BODY_SIZE, fontname="helv",
    )
    page.insert_text(
        (72, 190),
        "Here is the second body paragraph, a separate block.",
        fontsize=BODY_SIZE, fontname="helv",
    )
    return _save(doc, tmp_path / "simple.pdf")


@pytest.fixture
def multipage_pdf_path(tmp_path):
    """Four pages with a running header, page-number footers and a sentence hyphenated across pages 1-2."""
    bodies = [
        "Chapter text begins on the first page of the book.\n"
        "The narrator walks slowly along the river bank and\n"
        "keeps thinking about the conti-",
        "nuation of the sentence. The second page then goes on\n"
        "with more ordinary prose for the reader.",
        "The third page tells a different part of the story.\n"
        "Nothing special happens here at all.",
        "The fourth and final page closes the short book.\n"
        "The end comes quietly.",
    ]
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    for number, body in enumerate(bodies, start=1):
        page = _new_page(doc)
        page.insert_text((72, 30), "My Book", fontsize=9, fontname="helv")
        page.insert_text((72, 120), body, fontsize=BODY_SIZE, fontname="helv")
        page.insert_text((290, 820), str(number), fontsize=9, fontname="helv")
    return _save(doc, tmp_path / "multipage.pdf")


@pytest.fixture
def list_pdf_path(tmp_path):
    """One block made of three list lines."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = _new_page(doc)
    # Base-14 Helvetica uses a simple encoding that maps U+2022 to a middle dot
    # on extraction; embedding the same font keeps the bullet intact.
    page.insert_font(fontname="F0", fontbuffer=pymupdf.Font("helv").buffer)
    page.insert_text(
        (72, 120),
        "• first item\n• second item\n1) third item",
        fontsize=BODY_SIZE, fontname="F0",
    )
    return _save(doc, tmp_path / "list.pdf")


@pytest.fixture
def image_pdf_path(tmp_path):
    """A paragraph, a 200x100pt image, another paragraph, and a tiny 10x10pt image."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(
        (72, 120),
        "The paragraph above the picture describes the scene.",
        fontsize=BODY_SIZE, fontname="helv",
    )
    page.insert_image(pymupdf.Rect(72, 200, 272, 300), stream=_png_bytes(200, 100, (200, 40, 40)))
    page.insert_text(
        (72, 340),
        "The paragraph below the picture ends the page.",
        fontsize=BODY_SIZE, fontname="helv",
    )
    page.insert_image(pymupdf.Rect(400, 400, 410, 410), stream=_png_bytes(10, 10, (40, 40, 200)))
    return _save(doc, tmp_path / "image.pdf")


@pytest.fixture
def scanned_pdf_path(tmp_path):
    """Two pages that only contain an image, without any text layer."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    for color in ((230, 230, 230), (210, 210, 210)):
        page = _new_page(doc)
        page.insert_image(pymupdf.Rect(36, 36, 559, 806), stream=_png_bytes(300, 450, color))
    return _save(doc, tmp_path / "scanned.pdf")


@pytest.fixture
def encrypted_pdf_path(tmp_path):
    """A text PDF that requires a user password."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72, 120), "This document is protected by a password.", fontsize=BODY_SIZE, fontname="helv")
    return _save(
        doc,
        tmp_path / "encrypted.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="o",
        user_pw="u",
    )


# --- Formatting fixtures (tables, boxes, colours, font families) -------------

TEAL = (0, 0.537, 0.486)                    # "#00897c"
SLATE = (0.337, 0.388, 0.373)               # "#56635f"
PALE_FILL = (0.953, 0.965, 0.961)           # "#f3f6f5"
BAR_FILL = (0.604, 0.357, 0.176)            # "#9a5b2d"
RULE_FILL = (0.851, 0.878, 0.871)           # "#d9e0de"
TABLE_COLUMNS = (46, 101, 366, 550)


def _draw_column_rules(page, y: float) -> None:
    """Chrome-style row border: one 0.8pt filled rect per column segment, no vertical line."""
    pymupdf = _pymupdf()
    for x0, x1 in zip(TABLE_COLUMNS, TABLE_COLUMNS[1:]):
        page.draw_rect(pymupdf.Rect(x0, y - 0.4, x1, y + 0.4), color=None, fill=RULE_FILL)


@pytest.fixture
def formatted_pdf_path(tmp_path):
    """
    A two-page note mimicking a headless-Chrome print.

    Page 1: a white page-sized background rect; a title block (eyebrow in
    teal Courier 8pt, black Helvetica-Bold 21pt title, slate Helvetica 10pt
    subtitle) that PyMuPDF returns as one block; an opening paragraph; a
    callout box (#f3f6f5, 46..550 x 250..320) with a #9a5b2d left bar and two
    paragraphs; a 3-column table with per-column header fills (#f3f6f5) and
    per-column thin rules at y = 411, 478, 530 (no vertical lines, one empty
    cell in the last row); a "2 Second section" heading and a body paragraph.

    Page 2: a headerless continuation table with the same columns, its first
    row above the first rule (rules at y = 120, 170), then a body paragraph.
    """
    pymupdf = _pymupdf()
    doc = pymupdf.open()

    page = _new_page(doc)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_text((46, 60), "ACME - INTERNAL NOTE", fontsize=8, fontname="cour", color=TEAL)
    page.insert_text((46, 82), "Legal framework of the review", fontsize=21, fontname="hebo")
    page.insert_text(
        (46, 96), "Texts that apply to the weekly review tool.",
        fontsize=10, fontname="helv", color=SLATE,
    )
    page.insert_text(
        (46, 160), "The opening paragraph introduces the note and its purpose.",
        fontsize=10, fontname="helv",
    )

    page.draw_rect(pymupdf.Rect(46, 250, 550, 320), color=None, fill=PALE_FILL)
    page.draw_rect(pymupdf.Rect(46, 250, 48.2, 320), color=None, fill=BAR_FILL)
    page.insert_text(
        (56, 268), "The first callout paragraph explains the rule.", fontsize=10, fontname="helv",
    )
    page.insert_text(
        (56, 305), "The second callout paragraph gives an example.", fontsize=10, fontname="helv",
    )

    for x0, x1 in zip(TABLE_COLUMNS, TABLE_COLUMNS[1:]):
        page.draw_rect(pymupdf.Rect(x0, 391, x1, 411.8), color=None, fill=PALE_FILL)
    for y in (411, 478, 530):
        _draw_column_rules(page, y)
    for x, label in zip(TABLE_COLUMNS, ("ARTICLE", "OBLIGATION", "APPLICATION")):
        page.insert_text((x + 4, 405), label, fontsize=8, fontname="hebo")
    page.insert_text((50, 426), "L1222-3", fontsize=9, fontname="cour")
    page.insert_text(
        (105, 426), "The employer informs the employee\nbefore any monitoring.",
        fontsize=9, fontname="helv",
    )
    page.insert_text((370, 426), "Applies to the review tool.", fontsize=9, fontname="helv")
    page.insert_text((50, 493), "L1222-4", fontsize=9, fontname="cour")
    page.insert_text((105, 493), "No data may be collected secretly.", fontsize=9, fontname="helv")

    page.insert_text((46, 570), "2 Second section", fontsize=13, fontname="hebo")
    page.insert_text(
        (46, 600), "A normal body paragraph follows the second heading.", fontsize=10, fontname="helv",
    )

    page = _new_page(doc)
    page.insert_text((50, 110), "L1222-5", fontsize=9, fontname="cour")
    page.insert_text((105, 110), "Continued obligation text.", fontsize=9, fontname="helv")
    page.insert_text((370, 110), "Applies to every team.", fontsize=9, fontname="helv")
    page.insert_text((50, 150), "L1222-6", fontsize=9, fontname="cour")
    page.insert_text((105, 150), "Another obligation.", fontsize=9, fontname="helv")
    page.insert_text((370, 150), "Applies on request.", fontsize=9, fontname="helv")
    for y in (120, 170):
        _draw_column_rules(page, y)
    page.insert_text(
        (46, 220), "A body paragraph closes the second page of the note.", fontsize=10, fontname="helv",
    )
    return _save(doc, tmp_path / "formatted.pdf")


@pytest.fixture
def ruled_table_pdf_path(tmp_path):
    """A body paragraph above a fully stroked 3x3 table with one word per cell."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text(
        (72, 100), "The table below lists three fruits and their colours.",
        fontsize=BODY_SIZE, fontname="helv",
    )
    words = (("Fruit", "Colour", "Taste"), ("Apple", "Red", "Sweet"), ("Lemon", "Yellow", "Sour"))
    for row_index, row in enumerate(words):
        for column_index, word in enumerate(row):
            x0, y0 = 72 + 150 * column_index, 150 + 30 * row_index
            page.draw_rect(pymupdf.Rect(x0, y0, x0 + 150, y0 + 30), color=(0, 0, 0), width=0.5)
            page.insert_text((x0 + 6, y0 + 20), word, fontsize=BODY_SIZE, fontname="helv")
    return _save(doc, tmp_path / "ruled_table.pdf")
