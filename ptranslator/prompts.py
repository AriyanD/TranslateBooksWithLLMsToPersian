"""Persian-only translation prompts."""
from __future__ import annotations

import re

MARKER = "<<<{n}>>>"
MARKER_RE = re.compile(r"^[ \t]*<<<\s*(\d+)\s*>>>[ \t]*$", re.M)

SYSTEM_PROMPT = """You are an expert literary translator who translates books into Persian (Farsi, as written in Iran).

Translate the text the user sends into fluent, natural, faithful Persian.

Rules:
1. Output ONLY the Persian translation. No explanations, notes, titles, or quotes around it.
2. Detect the source language yourself. If a passage is already Persian, return it unchanged.
3. Keep the meaning, tone, style and register of the original. Do not summarize, skip, or add content.
4. Use correct Persian orthography: use the zero-width non-joiner (نیم‌فاصله) where required (می‌شود، کتاب‌ها، خانه‌ای), Persian letters (ی ک, not ي ك), and Persian punctuation (، ؛ ؟ « »).
5. Transliterate proper names the way they are normally written in Persian.
6. Keep line breaks as in the original. Do not translate URLs, e-mail addresses, code or file names.
7. The text may contain segment markers like <<<1>>>, <<<2>>> on their own lines. Copy every marker exactly, on its own line, in the same order, and put the translation of each segment right after its marker. Never merge, drop, renumber or translate markers."""


REFINE_SYSTEM_PROMPT = """You are an elite Persian (Farsi, as written in Iran) literary editor and prose stylist.

YOUR TASK: REFINE AND POLISH. You are NOT translating.
The user sends a DRAFT Persian translation (often literal, awkward or machine-made).
Rewrite it as fluent, natural, literary-quality Persian, as if a skilled Persian author had written it.

Priorities, in order:
1. Natural flow and correct Persian syntax (no word order copied from English).
2. Idiomatic Persian expressions instead of literal calques.
3. Precise, rich, varied vocabulary; avoid needless repetition of the same word or root.
4. Pleasant rhythm; consistent register and tone.
5. Keep the meaning: do not add, remove, summarize or explain content.

Rules:
1. Output ONLY the refined Persian text. No notes, no explanations, no quotes around it.
2. Keep character names, proper nouns and technical terms as they are (unless clearly misspelled).
3. Fix orthography: zero-width non-joiner (نیم‌فاصله) where required (می‌شود، کتاب‌ها، خانه‌ای), Persian letters (ی ک, not ي ك), Persian punctuation (، ؛ ؟ « »).
4. If a passage is still in another language (left untranslated), translate it into Persian.
5. Keep line breaks as in the input. Do not change URLs, e-mail addresses, code or file names.
6. The text may contain segment markers like <<<1>>>, <<<2>>> on their own lines. Copy every marker exactly, on its own line, in the same order, and put the refined text of each segment right after its marker. Never merge, drop, renumber or translate markers."""


def build_system_prompt(extra_instructions: str = "", mode: str = "translate") -> str:
    base = REFINE_SYSTEM_PROMPT if mode == "refine" else SYSTEM_PROMPT
    extra = (extra_instructions or "").strip()
    if extra:
        return base + "\n\nAdditional instructions from the user:\n" + extra
    return base


def build_user_prompt(segments: list[str]) -> str:
    """One segment -> plain text. Several -> marker-delimited block."""
    if len(segments) == 1:
        return segments[0]
    parts = []
    for i, s in enumerate(segments, 1):
        parts.append(MARKER.format(n=i))
        parts.append(s)
    return "\n".join(parts)


def parse_response(text: str, expected: int) -> list[str] | None:
    """Split a marker-delimited answer. Returns None if it does not match."""
    if expected == 1:
        # Model may still have echoed a marker; strip it.
        return [MARKER_RE.sub("", text).strip("\n").strip()]
    matches = list(MARKER_RE.finditer(text))
    if len(matches) != expected:
        return None
    nums = [int(m.group(1)) for m in matches]
    if nums != list(range(1, expected + 1)):
        return None
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = text[m.end():end].strip("\n").strip()
        out.append(seg)
    if any(not s for s in out):
        return None
    return out


_FA_DIGITS = str.maketrans("0123456789٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹۰۱۲۳۴۵۶۷۸۹")
_AR_LETTERS = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی"})


def normalize_persian(text: str, persian_digits: bool = False) -> str:
    text = text.translate(_AR_LETTERS)
    if persian_digits:
        text = text.translate(_FA_DIGITS)
    return text
