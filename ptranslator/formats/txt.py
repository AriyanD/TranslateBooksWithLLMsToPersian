from __future__ import annotations

import re

from .base import Document, is_translatable, split_long

MAX_PARAGRAPH = 4000


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def load_txt(data: bytes) -> Document:
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
    # Keep blank-line separators exactly as they are.
    parts = re.split(r"(\n\s*\n)", text)
    layout = []          # list of ("sep", str) or ("seg", [segment ids], joiner)
    segments: list[str] = []
    for i, p in enumerate(parts):
        if i % 2 == 1 or not is_translatable(p):
            layout.append(("raw", p))
            continue
        lead = p[: len(p) - len(p.lstrip())]
        trail = p[len(p.rstrip()):]
        body = p.strip()
        pieces = split_long(body, MAX_PARAGRAPH)
        ids = []
        for piece in pieces:
            ids.append(len(segments))
            segments.append(piece)
        joiner = "\n" if "\n" in body else " "
        layout.append(("seg", ids, joiner, lead, trail))

    def build(tr: dict) -> bytes:
        out = []
        for item in layout:
            if item[0] == "raw":
                out.append(item[1])
            else:
                _, ids, joiner, lead, trail = item
                out.append(lead + joiner.join(tr.get(i, segments[i]) for i in ids) + trail)
        return "".join(out).encode("utf-8")

    return Document(segments, build, ".txt")
