"""
Upload security and language detection for PDF files.

PDFs are generated inline with PyMuPDF (no dependency on tests/test_pdf).
Every SecureFileHandler writes into a pytest tmp dir, never the real upload dir.
"""
import io

import pytest

pymupdf = pytest.importorskip("pymupdf")

from src.utils.language_detector import LANGUAGE_CODE_MAP, LanguageDetector
from src.utils.security import SecureFileHandler


ENGLISH_PROSE = (
    "The old lighthouse keeper climbed the narrow stairs every evening before sunset. "
    "He carried a small lantern and a notebook in which he wrote down the weather, "
    "the ships he had seen during the day, and the names of the birds that rested on the rocks. "
    "Nobody in the village remembered when he had first arrived, but everyone agreed that "
    "the light had never failed while he was there. On stormy nights the fishermen looked "
    "toward the cliff and felt safe, because they knew that someone was watching over the sea. "
    "When the keeper finally grew too old to climb the stairs, the children of the village "
    "took turns helping him, and they listened to his stories until the stars came out."
)


# --- Inline PDF generators ---------------------------------------------------


def _text_pdf_bytes(text: str = ENGLISH_PROSE, pages: int = 1) -> bytes:
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page()
            page.insert_textbox(pymupdf.Rect(72, 72, 523, 770), text, fontsize=11)
        return doc.tobytes()
    finally:
        doc.close()


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (180, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _scanned_pdf_bytes(pages: int = 2) -> bytes:
    png = _png_bytes()
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page()
            page.insert_image(pymupdf.Rect(72, 72, 472, 472), stream=png)
        return doc.tobytes()
    finally:
        doc.close()


def _encrypted_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 770), ENGLISH_PROSE, fontsize=11)
        buffer = io.BytesIO()
        doc.save(buffer, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        return buffer.getvalue()
    finally:
        doc.close()


@pytest.fixture
def handler(tmp_path):
    return SecureFileHandler(tmp_path / "uploads")


def _upload_dir_files(handler):
    return sorted(p.name for p in handler.upload_dir.iterdir())


# --- Validation ----------------------------------------------------------------


def test_valid_pdf_is_accepted_without_warnings(handler):
    result = handler.validate_and_save_file(_text_pdf_bytes(), "x.pdf")

    assert result.is_valid, result.error_message
    assert result.warnings == []
    assert result.file_path is not None and result.file_path.exists()
    assert result.file_path.name.endswith("_x.pdf")
    assert result.file_path.parent == handler.upload_dir.resolve()


def test_non_pdf_bytes_with_pdf_extension_are_rejected(handler):
    result = handler.validate_and_save_file(b"hello", "x.pdf")

    assert not result.is_valid
    assert "%PDF" in result.error_message
    # The temporary file is removed on rejection
    assert _upload_dir_files(handler) == []


def test_encrypted_pdf_is_rejected(handler):
    result = handler.validate_and_save_file(_encrypted_pdf_bytes(), "x.pdf")

    assert not result.is_valid
    assert "Password-protected" in result.error_message
    # The document was closed, so the temporary file could be removed
    assert _upload_dir_files(handler) == []


def test_corrupt_pdf_is_rejected(handler):
    result = handler.validate_and_save_file(b"%PDF-1.4\ngarbage garbage\n%%EOF\n", "x.pdf")

    assert not result.is_valid
    assert result.error_message.startswith("PDF validation failed:")
    assert _upload_dir_files(handler) == []


def test_scanned_pdf_is_accepted_with_a_warning(handler):
    result = handler.validate_and_save_file(_scanned_pdf_bytes(), "x.pdf")

    assert result.is_valid, result.error_message
    assert len(result.warnings) == 1
    assert "no text layer" in result.warnings[0]


def test_pdf_with_unknown_extension_is_routed_to_pdf_validator(handler, monkeypatch):
    calls = []
    original = SecureFileHandler._validate_pdf_file

    def spy(self, file_path):
        calls.append(file_path)
        return original(self, file_path)

    monkeypatch.setattr(SecureFileHandler, "_validate_pdf_file", spy)

    result = handler.validate_and_save_file(_text_pdf_bytes(), "x.bin")

    assert result.is_valid, result.error_message
    assert len(calls) == 1


def test_pdf_validator_never_raises_on_pymupdf_errors(handler, monkeypatch, tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(_text_pdf_bytes())

    def boom(*args, **kwargs):
        raise RuntimeError("native failure")

    monkeypatch.setattr(pymupdf, "open", boom)

    result = handler._validate_pdf_file(path)

    assert not result.is_valid
    assert result.error_message == "PDF validation failed: native failure"


# --- Language detection ----------------------------------------------------------


def test_language_detection_on_english_pdf():
    language, confidence = LanguageDetector.detect_language_from_file(_text_pdf_bytes(), "a.pdf")

    assert language == LANGUAGE_CODE_MAP["en"]
    assert confidence > 0.0


def test_language_detection_on_scanned_pdf_returns_none():
    assert LanguageDetector.detect_language_from_file(_scanned_pdf_bytes(), "a.pdf") == (None, 0.0)


def test_language_detection_on_invalid_pdf_never_raises():
    assert LanguageDetector.detect_language_from_file(b"hello", "a.pdf") == (None, 0.0)
