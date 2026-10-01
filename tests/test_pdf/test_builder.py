"""Tests for the PDF builder (translated paragraphs -> reflowed PDF)."""

import pymupdf
import pytest

import src.config
from src.core.pdf.builder import build_pdf
from src.core.pdf.content import PdfBuildError, PdfImage, PdfPlainContent


def _content(texts, styles=None, **kwargs):
    """Build a PdfPlainContent by hand."""
    return PdfPlainContent(
        paragraphs_text=list(texts),
        paragraphs_style=list(styles) if styles else ["normal"] * len(texts),
        **kwargs,
    )


def _png(width=40, height=20):
    """Create a small solid-colour PNG without external imaging libraries."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix.tobytes("png")


def _full_text(path):
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def _text_blocks(path):
    """Return the text blocks (type 0) of the first page."""
    with pymupdf.open(path) as doc:
        return [b for b in doc[0].get_text("dict")["blocks"] if b["type"] == 0]


def test_styles_render_in_order_with_heading_size(tmp_path):
    out = str(tmp_path / "out.pdf")
    content = _content(
        ["Heading text", "Body text", "List text"],
        ["heading1", "normal", "list"],
    )
    build_pdf(["Heading text", "Body text", "List text"], content, out, "French")

    text = _full_text(out)
    assert text.index("Heading text") < text.index("Body text") < text.index("List text")

    sizes = {}
    for block in _text_blocks(out):
        for line in block["lines"]:
            for span in line["spans"]:
                sizes[span["text"].strip()] = span["size"]
    assert sizes["Heading text"] == pytest.approx(20, abs=0.5)
    assert sizes["Body text"] == pytest.approx(11, abs=0.5)


def test_output_uses_source_page_size(tmp_path):
    out = str(tmp_path / "out.pdf")
    texts = [f"Paragraph {i} " + "word " * 80 for i in range(40)]
    content = _content(texts, page_size=(420, 595))
    build_pdf(texts, content, out, "French")

    with pymupdf.open(out) as doc:
        assert doc.page_count > 1
        for page in doc:
            assert page.rect.width == 420
            assert page.rect.height == 595


def test_many_paragraphs_span_multiple_pages(tmp_path):
    out = str(tmp_path / "out.pdf")
    texts = [f"P{i:03d} " + ("lorem " * 66)[:395] for i in range(300)]
    assert all(len(t) >= 395 for t in texts)
    content = _content(texts)
    build_pdf(texts, content, out, "French")

    with pymupdf.open(out) as doc:
        assert doc.page_count > 1
    assert "P299" in _full_text(out)


def test_rtl_target_is_right_aligned_and_ltr_is_left_aligned(tmp_path):
    width = 595.0
    content = _content(["short"], page_size=(width, 842.0))

    rtl_out = str(tmp_path / "rtl.pdf")
    build_pdf(["שלום עולם"], content, rtl_out, "Hebrew")
    rtl_blocks = _text_blocks(rtl_out)
    assert rtl_blocks
    assert rtl_blocks[0]["bbox"][0] > width / 2

    ltr_out = str(tmp_path / "ltr.pdf")
    build_pdf(["Bonjour le monde"], content, ltr_out, "French")
    ltr_blocks = _text_blocks(ltr_out)
    assert ltr_blocks
    assert ltr_blocks[0]["bbox"][0] < width / 2


def test_cjk_text_round_trips(tmp_path):
    out = str(tmp_path / "out.pdf")
    translated = ["这是一个中文段落。", "日本語の文章です。"]
    content = _content(["a", "b"])
    build_pdf(translated, content, out, "Chinese")

    text = _full_text(out)
    for paragraph in translated:
        assert paragraph in text


def test_bilingual_places_source_before_translation(tmp_path):
    out = str(tmp_path / "out.pdf")
    sources = ["Hello world", "Good morning"]
    translated = ["Bonjour monde", "Bon matin"]
    content = _content(sources)
    build_pdf(translated, content, out, "French", source_language="English", bilingual=True)

    text = _full_text(out)
    positions = [
        text.index("Hello world"),
        text.index("Bonjour monde"),
        text.index("Good morning"),
        text.index("Bon matin"),
    ]
    assert positions == sorted(positions)


def test_bilingual_source_text_is_grey(tmp_path):
    out = str(tmp_path / "out.pdf")
    content = _content(["Hello world"])
    build_pdf(["Bonjour monde"], content, out, "French", source_language="English", bilingual=True)

    colors = {}
    for block in _text_blocks(out):
        for line in block["lines"]:
            for span in line["spans"]:
                colors[span["text"].strip()] = span["color"]
    assert colors["Hello world"] == 0x666666
    assert colors["Bonjour monde"] == 0


def test_oversized_image_is_scaled_to_content_width(tmp_path):
    out = str(tmp_path / "out.pdf")
    page_w, page_h = 595.0, 842.0
    margin = min(72.0, max(36.0, 0.1 * min(page_w, page_h)))
    cw = page_w - 2 * margin

    image = PdfImage(data=_png(), ext="png", width_pt=2000.0, height_pt=1000.0)
    content = _content(
        ["Intro paragraph"],
        images_by_paragraph={0: [image]},
        page_size=(page_w, page_h),
    )
    build_pdf(["Intro paragraph"], content, out, "French")

    with pymupdf.open(out) as doc:
        image_blocks = [
            b for page in doc for b in page.get_text("dict")["blocks"] if b["type"] == 1
        ]
    assert len(image_blocks) == 1
    bbox = image_blocks[0]["bbox"]
    assert bbox[2] - bbox[0] <= cw + 1


def test_images_follow_their_anchor_paragraph(tmp_path):
    out = str(tmp_path / "out.pdf")
    before = PdfImage(data=_png(), ext="png", width_pt=100.0, height_pt=50.0)
    after = PdfImage(data=_png(30, 30), ext="png", width_pt=60.0, height_pt=60.0)
    content = _content(
        ["First paragraph", "Second paragraph"],
        images_by_paragraph={-1: [before], 0: [after]},
    )
    build_pdf(["First paragraph", "Second paragraph"], content, out, "French")

    with pymupdf.open(out) as doc:
        blocks = doc[0].get_text("dict")["blocks"]
    kinds = [
        "image" if b["type"] == 1
        else b["lines"][0]["spans"][0]["text"].strip()
        for b in sorted(blocks, key=lambda b: b["bbox"][1])
    ]
    assert kinds == ["image", "First paragraph", "image", "Second paragraph"]


def test_length_mismatch_raises_value_error(tmp_path):
    content = _content(["one", "two"])
    with pytest.raises(ValueError, match=r"1.*2"):
        build_pdf(["only one"], content, str(tmp_path / "out.pdf"), "French")


@pytest.mark.parametrize("enabled", [True, False])
def test_attribution_metadata(tmp_path, monkeypatch, enabled):
    monkeypatch.setattr(src.config, "ATTRIBUTION_ENABLED", enabled)
    out = str(tmp_path / "out.pdf")
    content = _content(["Hello"], title="My Title", author="An Author")
    build_pdf(["Bonjour"], content, out, "French")

    with pymupdf.open(out) as doc:
        meta = doc.metadata
    expected = src.config.GENERATOR_NAME if enabled else ""
    assert meta["producer"] == expected
    assert meta["creator"] == expected
    assert meta["title"] == "My Title"
    assert meta["author"] == "An Author"


def test_html_in_text_is_rendered_literally(tmp_path):
    out = str(tmp_path / "out.pdf")
    content = _content(["src"])
    build_pdf(["<b>x</b> & y"], content, out, "French")

    assert "<b>x</b> & y" in _full_text(out)


def test_all_empty_translation_writes_valid_single_page_pdf(tmp_path):
    out = str(tmp_path / "out.pdf")
    content = _content(["a", "b"])
    build_pdf(["", "   "], content, out, "French")

    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


def test_empty_content_writes_valid_pdf(tmp_path):
    out = str(tmp_path / "out.pdf")
    build_pdf([], PdfPlainContent(), out, "French")

    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        assert doc[0].rect.width == pytest.approx(595, abs=1)


def test_save_failure_is_wrapped_in_pdf_build_error(tmp_path):
    content = _content(["Hello"])
    missing_dir = tmp_path / "does_not_exist" / "out.pdf"
    with pytest.raises(PdfBuildError):
        build_pdf(["Bonjour"], content, str(missing_dir), "French")


def test_layout_guard_raises_pdf_build_error(tmp_path, monkeypatch):
    import src.core.pdf.builder as builder

    monkeypatch.setattr(builder, "_PAGES_PER_ITEM", 0)
    monkeypatch.setattr(builder, "_PAGES_SLACK", 0)
    content = _content(["Hello"])
    with pytest.raises(PdfBuildError, match="did not converge"):
        build_pdf(["Bonjour"], content, str(tmp_path / "out.pdf"), "French")
