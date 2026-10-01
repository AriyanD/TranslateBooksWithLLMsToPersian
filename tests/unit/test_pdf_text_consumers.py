"""PDF support in the auxiliary text consumers (sampler, auto-prep, cost, sample)."""

import io
from pathlib import Path

import pytest

from src.api.blueprints import cost_routes, sample_routes
from src.core import auto_prep
from src.utils import document_sampler

pymupdf = pytest.importorskip("pymupdf")

_PARAGRAPH = (
    "The quick brown fox jumps over the lazy dog while the village sleeps "
    "quietly under a pale winter moon and the river runs on."
)


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def text_pdf_path(tmp_path) -> str:
    doc = pymupdf.open()
    for page_number in (1, 2):
        page = doc.new_page(width=595, height=842)
        page.insert_text(
            (72, 100),
            f"Page {page_number}.\n{_PARAGRAPH}\n{_PARAGRAPH}",
            fontsize=11,
            fontname="helv",
        )
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def scanned_pdf_path(tmp_path) -> str:
    doc = pymupdf.open()
    png = _png_bytes()
    for _ in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=png)
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


class TestTextPdf:
    def test_document_sampler_extract_full_text(self, text_pdf_path):
        data = Path(text_pdf_path).read_bytes()
        text = document_sampler.extract_full_text(data, "a.pdf")
        assert text and "quick brown fox" in text

    def test_document_sampler_supports_pdf(self):
        assert ".pdf" in document_sampler.RICH_EXTS
        assert ".pdf" in document_sampler.SUPPORTED_EXTS

    def test_auto_prep_extract_source_text(self, text_pdf_path):
        text = auto_prep.extract_source_text(file_path=text_pdf_path)
        assert text and "quick brown fox" in text

    def test_cost_routes_extract_text_for_estimation(self, text_pdf_path):
        text = cost_routes._extract_text_for_estimation(Path(text_pdf_path))
        assert text and "quick brown fox" in text

    def test_sample_routes_extract_plain_text(self, text_pdf_path):
        text = sample_routes._extract_plain_text(text_pdf_path, "pdf")
        assert text and "quick brown fox" in text


class TestScannedPdf:
    def test_document_sampler_returns_none(self, scanned_pdf_path):
        data = Path(scanned_pdf_path).read_bytes()
        assert document_sampler.extract_full_text(data, "a.pdf") is None

    def test_auto_prep_returns_empty(self, scanned_pdf_path):
        assert auto_prep.extract_source_text(file_path=scanned_pdf_path) == ""

    def test_cost_routes_returns_empty(self, scanned_pdf_path):
        assert cost_routes._extract_text_for_estimation(Path(scanned_pdf_path)) == ""

    def test_sample_routes_returns_empty(self, scanned_pdf_path):
        assert sample_routes._extract_plain_text(scanned_pdf_path, "pdf") == ""
