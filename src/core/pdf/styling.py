"""
Styling helpers for PDF translation (PyMuPDF span data -> font family, bold/italic, colour).

Pure, never-raising helpers used by the extractor to describe how a paragraph
looked in the source PDF:
- FontResolver turns a span font name into the real font name. Type3 fonts show up as
  "Type3 (16 0 R)"; their real name lives in the FontDescriptor object.
- classify_family maps a font name (and, when meaningful, the span flags) to a CSS
  generic family: 'serif', 'sans-serif' or 'monospace'.
- is_bold / is_italic combine the span flag bits with font name tokens.
- Colour helpers convert PyMuPDF colour values to lowercase '#rrggbb' strings and
  classify them (near-black = default text colour, light = unreadable on white).

The input PDF is untrusted: FontResolver.resolve never raises and falls back to the
span font name. This module does not import pymupdf; it receives an already-open doc.
"""
import logging
import re
from typing import Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

TYPE3_NAME_RE = re.compile(r"^Type3 \((\d+) 0 R\)$")
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
XREF_VALUE_RE = re.compile(r"^\s*(\d+)\s+0\s+R\s*$")
HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$")

ITALIC_FLAG, SERIF_FLAG, MONO_FLAG, BOLD_FLAG = 2, 4, 8, 16   # PyMuPDF span flag bits
NEAR_BLACK_MAX = 0x40          # every channel <= this -> treated as default text colour
LIGHT_MIN = 0xD0               # every channel >= this -> light colour

MONO_TOKENS = ("mono", "courier", "consol", "menlo", "inconsolata", "code")
SANS_TOKENS = ("sans", "helvetica", "arial", "verdana", "tahoma", "segoe", "calibri",
               "roboto", "inter", "lato", "frutiger", "futura", "gothic", "nimbussans")
SERIF_TOKENS = ("serif", "times", "roman", "georgia", "garamond", "minion", "palatino",
                "cambria", "baskerville", "charis", "mincho", "song", "ming")
BOLD_TOKENS = ("bold", "black", "heavy")
ITALIC_TOKENS = ("italic", "oblique")

FAMILY_MONOSPACE = "monospace"
FAMILY_SANS_SERIF = "sans-serif"
FAMILY_SERIF = "serif"


# --- Font names --------------------------------------------------------------

def _is_pair(value) -> bool:
    """True when value is a (type, value) pair as returned by xref_get_key."""
    return isinstance(value, (tuple, list)) and len(value) == 2


class FontResolver:
    """Resolves span font names to real font names, with a per-document cache."""

    def __init__(self, doc) -> None:
        self._doc = doc
        self._cache: Dict[str, str] = {}

    def is_type3(self, span_font: str) -> bool:
        """True when the span font name is a Type3 reference like 'Type3 (16 0 R)'."""
        return isinstance(span_font, str) and TYPE3_NAME_RE.match(span_font) is not None

    def resolve(self, span_font: str) -> str:
        """Return the real font name for a span font name (subset prefix removed). Never raises."""
        if not isinstance(span_font, str):
            return ""
        cached = self._cache.get(span_font)
        if cached is not None:
            return cached
        try:
            resolved = self._resolve_uncached(span_font)
        except Exception as exc:  # untrusted PDF: never fail on a font lookup
            logger.debug("Font resolution failed for %r: %s", span_font, exc)
            resolved = span_font
        resolved = SUBSET_PREFIX_RE.sub("", resolved)
        self._cache[span_font] = resolved
        return resolved

    def _resolve_uncached(self, span_font: str) -> str:
        """Follow a Type3 font's FontDescriptor to its FontName; else return the input."""
        match = TYPE3_NAME_RE.match(span_font)
        if match is None:
            return span_font
        descriptor = self._doc.xref_get_key(int(match.group(1)), "FontDescriptor")
        if not _is_pair(descriptor) or descriptor[0] != "xref":
            return span_font
        ref = XREF_VALUE_RE.match(str(descriptor[1]))
        if ref is None:
            return span_font
        font_name = self._doc.xref_get_key(int(ref.group(1)), "FontName")
        if not _is_pair(font_name) or font_name[0] != "name":
            return span_font
        return str(font_name[1]).lstrip("/") or span_font


def normalize_font_name(name: str) -> str:
    """Lower-case a font name and keep only [a-z0-9]."""
    if not isinstance(name, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _as_int(value) -> int:
    """Coerce span flags to int; anything unusable counts as 0."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def classify_family(font_name: str, flags: int, *, use_flags: bool = True) -> Optional[str]:
    """
    Map a font name to 'monospace', 'sans-serif', 'serif' or None (unknown).

    Name tokens win, tested mono -> sans -> serif (so 'sansserif' is sans). With no
    token match, the span flags decide unless use_flags is False (an unresolved Type3
    name carries meaningless flags).
    """
    normalized = normalize_font_name(font_name)
    if any(token in normalized for token in MONO_TOKENS):
        return FAMILY_MONOSPACE
    if any(token in normalized for token in SANS_TOKENS):
        return FAMILY_SANS_SERIF
    if any(token in normalized for token in SERIF_TOKENS):
        return FAMILY_SERIF
    if not use_flags:
        return None
    flags = _as_int(flags)
    if flags & MONO_FLAG:
        return FAMILY_MONOSPACE
    if flags & SERIF_FLAG:
        return FAMILY_SERIF
    return None


def is_bold(font_name: str, flags: int) -> bool:
    """True when the bold flag is set or the font name carries a bold token."""
    if _as_int(flags) & BOLD_FLAG:
        return True
    normalized = normalize_font_name(font_name)
    return any(token in normalized for token in BOLD_TOKENS)


def is_italic(font_name: str, flags: int) -> bool:
    """True when the italic flag is set or the font name carries an italic token."""
    if _as_int(flags) & ITALIC_FLAG:
        return True
    normalized = normalize_font_name(font_name)
    return any(token in normalized for token in ITALIC_TOKENS)


# --- Colours -----------------------------------------------------------------

def color_int_to_hex(color: int) -> str:
    """Convert a PyMuPDF sRGB integer (0xRRGGBB) to '#rrggbb'."""
    return "#{:06x}".format(_as_int(color) & 0xFFFFFF)


def fill_to_hex(fill: Sequence[float]) -> str:
    """Convert an RGB float triple (0..1) to '#rrggbb'; channels are rounded and clamped."""
    channels = []
    for index in range(3):
        try:
            value = round(float(fill[index]) * 255)
        except (TypeError, ValueError, IndexError, OverflowError):
            value = 0
        channels.append(max(0, min(255, value)))
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _hex_channels(hex_color: str) -> Optional[Tuple[int, int, int]]:
    """Parse '#rrggbb' into three ints, or None when malformed."""
    if not isinstance(hex_color, str):
        return None
    match = HEX_COLOR_RE.match(hex_color)
    if match is None:
        return None
    return tuple(int(group, 16) for group in match.groups())


def is_near_black(hex_color: str) -> bool:
    """True when every channel is <= NEAR_BLACK_MAX (default text colour)."""
    channels = _hex_channels(hex_color)
    return channels is not None and all(c <= NEAR_BLACK_MAX for c in channels)


def is_light(hex_color: str) -> bool:
    """True when every channel is >= LIGHT_MIN (unreadable on a white page)."""
    channels = _hex_channels(hex_color)
    return channels is not None and all(c >= LIGHT_MIN for c in channels)
