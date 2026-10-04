"""File formats. Each loader returns a Document with a flat list of text
segments and a ``build(translations)`` method that writes the Persian file."""
from __future__ import annotations

import os

from .base import Document
from .txt import load_txt
from .srt import load_srt
from .epub import load_epub
from .docx import load_docx
from .pdf import load_pdf

SUPPORTED = {".txt": load_txt, ".md": load_txt, ".srt": load_srt,
             ".epub": load_epub, ".docx": load_docx,
             ".pdf": load_pdf}


def load_document(filename: str, data: bytes) -> Document:
    ext = os.path.splitext(filename)[1].lower()
    loader = SUPPORTED.get(ext)
    if not loader:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED)}")
    return loader(data)
