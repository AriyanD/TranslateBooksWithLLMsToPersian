from __future__ import annotations

import re
from typing import Callable

# Segments that need no translation (numbers, punctuation, page numbers...)
_NO_LETTERS = re.compile(r"^[\W\d_]*$", re.U)


def is_translatable(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not _NO_LETTERS.match(t)


class Document:
    def __init__(self, segments: list[str], builder: Callable[[dict], bytes], ext: str):
        self.segments = segments
        self._builder = builder
        self.ext = ext

    def build(self, translations: dict[int, str]) -> bytes:
        """translations: {segment index: Persian text}. Missing ones keep the original."""
        return self._builder(translations)


def split_long(text: str, max_chars: int) -> list[str]:
    """Split an over-long block by lines, then by sentences."""
    if len(text) <= max_chars:
        return [text]
    pieces, cur = [], ""
    units = text.split("\n")
    if len(units) == 1:
        units = re.split(r"(?<=[.!?…。！？])\s+", text)
    sep = "\n" if "\n" in text else " "
    for u in units:
        if cur and len(cur) + len(u) + 1 > max_chars:
            pieces.append(cur)
            cur = u
        else:
            cur = f"{cur}{sep}{u}" if cur else u
    if cur:
        pieces.append(cur)
    return pieces
