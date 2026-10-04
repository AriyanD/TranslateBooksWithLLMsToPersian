from __future__ import annotations

import re

from .base import Document, is_translatable
from .txt import _decode

_TIME = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}")


def load_srt(data: bytes) -> Document:
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    blocks = re.split(r"\n\s*\n", text)
    parsed = []          # (header_lines, text or None, seg_id or None)
    segments: list[str] = []
    for b in blocks:
        lines = b.split("\n")
        t_idx = next((k for k, l in enumerate(lines) if _TIME.search(l)), None)
        if t_idx is None:
            parsed.append((lines, None, None))
            continue
        header = lines[: t_idx + 1]
        # drop RTL marks we (or others) added, so refining a .fa.srt does not double them
        body = "\n".join(lines[t_idx + 1:]).replace("\u200f", "")
        if is_translatable(body):
            parsed.append((header, body, len(segments)))
            segments.append(body)
        else:
            parsed.append((header, body, None))

    def build(tr: dict) -> bytes:
        out = []
        for header, body, sid in parsed:
            if body is None:
                out.append("\n".join(header))
                continue
            new_body = tr.get(sid, body) if sid is not None else body
            # RLE/PDF-free: most players render Persian fine; force RTL with RLM at line start
            new_body = "\n".join(("\u200f" + l) if sid is not None and l else l
                                 for l in new_body.split("\n"))
            out.append("\n".join(header) + ("\n" + new_body if new_body else ""))
        return ("\n\n".join(out) + "\n").encode("utf-8")

    return Document(segments, build, ".srt")
