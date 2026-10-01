"""
PDF translation module.

PyMuPDF is imported lazily (inside the wrappers below) so that this package,
and its data model, can be imported even when PyMuPDF is not installed.
"""

from typing import Any, Dict, List, Optional, Union

from .content import (
    FONT_FAMILIES,
    PARAGRAPH_STYLES,
    ParagraphFormat,
    PdfBox,
    PdfBuildError,
    PdfEncryptedError,
    PdfError,
    PdfImage,
    PdfNoTextLayerError,
    PdfOpenError,
    PdfPlainContent,
    PdfTable,
)


def extract_pdf_paragraphs(
    source: Union[str, bytes], *, include_images: bool = True, detect_layout: bool = True,
) -> PdfPlainContent:
    """Extract paragraphs, styles, formats, tables and images from a PDF (see extractor.extract_pdf_paragraphs)."""
    from .extractor import extract_pdf_paragraphs as _extract_pdf_paragraphs

    return _extract_pdf_paragraphs(source, include_images=include_images, detect_layout=detect_layout)


def extract_pdf_text(source: Union[str, bytes], hard_cap: Optional[int] = None) -> str:
    """Return the PDF's plain text, or "" on any failure, including a missing PyMuPDF. Never raises."""
    try:
        from .extractor import extract_pdf_text as _extract_pdf_text
    except Exception:
        return ""
    return _extract_pdf_text(source, hard_cap=hard_cap)


def build_pdf(
    translated_paragraphs: List[str],
    content: PdfPlainContent,
    output_path: str,
    target_language: str,
    source_language: str = "",
    bilingual: bool = False,
) -> None:
    """Write a reflowed PDF from translated paragraphs (see builder.build_pdf)."""
    from .builder import build_pdf as _build_pdf

    _build_pdf(
        translated_paragraphs, content, output_path, target_language,
        source_language=source_language, bilingual=bilingual,
    )


async def translate_pdf_file(*args, **kwargs) -> Dict[str, Any]:
    """Translate a PDF into a reflowed PDF (see translator.translate_pdf_file)."""
    from .translator import translate_pdf_file as _translate_pdf_file

    return await _translate_pdf_file(*args, **kwargs)


__all__ = [
    'FONT_FAMILIES',
    'PARAGRAPH_STYLES',
    'ParagraphFormat',
    'PdfBox',
    'PdfBuildError',
    'PdfEncryptedError',
    'PdfError',
    'PdfImage',
    'PdfNoTextLayerError',
    'PdfOpenError',
    'PdfPlainContent',
    'PdfTable',
    'build_pdf',
    'extract_pdf_paragraphs',
    'extract_pdf_text',
    'translate_pdf_file',
]
