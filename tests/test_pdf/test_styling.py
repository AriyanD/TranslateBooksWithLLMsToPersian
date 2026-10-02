"""Tests for the PDF styling helpers (font family, bold/italic, colours)."""
import pytest

from src.core.pdf.styling import (
    FontResolver,
    classify_family,
    color_int_to_hex,
    fill_to_hex,
    is_bold,
    is_italic,
    is_light,
    is_near_black,
    normalize_font_name,
)


class StubDoc:
    """Minimal stand-in for an open pymupdf.Document."""

    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error
        self.calls = 0

    def xref_get_key(self, xref, key):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.values.get((xref, key), ("null", "null"))


def _type3_doc():
    return StubDoc({
        (16, "FontDescriptor"): ("xref", "601 0 R"),
        (601, "FontName"): ("name", "/HAAAAA+IBM-Plex-Sans"),
    })


# --- classify_family ---------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("IBM-Plex-Sans", "sans-serif"),
    ("IBMPlexMono-Medium", "monospace"),
    ("DejaVuSansMono", "monospace"),
    ("NotoSerif-Regular", "serif"),
    ("Times-Roman", "serif"),
    ("Helvetica-Bold", "sans-serif"),
    ("MS Gothic", "sans-serif"),
    ("SansSerif", "sans-serif"),
])
def test_classify_family_from_name(name, expected):
    assert classify_family(name, 0) == expected


def test_classify_family_falls_back_to_flags():
    assert classify_family("XyzUnknown", 4) == "serif"
    assert classify_family("XyzUnknown", 8) == "monospace"
    assert classify_family("XyzUnknown", 0) is None


def test_classify_family_ignores_flags_when_disabled():
    assert classify_family("XyzUnknown", 4, use_flags=False) is None
    assert classify_family("XyzUnknown", 8, use_flags=False) is None
    # Name tokens still apply
    assert classify_family("Arial", 4, use_flags=False) == "sans-serif"


def test_classify_family_name_wins_over_flags():
    assert classify_family("Helvetica", 4) == "sans-serif"


def test_classify_family_tolerates_malformed_input():
    assert classify_family(None, None) is None
    assert classify_family("", "garbage") is None


def test_normalize_font_name():
    assert normalize_font_name("IBM-Plex Sans_Bold") == "ibmplexsansbold"
    assert normalize_font_name(None) == ""


# --- is_bold / is_italic -----------------------------------------------------

def test_is_bold():
    assert is_bold("IBM-Plex-Sans-Bold", 0)
    assert is_bold("Foo-SemiBold", 0)
    assert is_bold("Foo-ExtraBold", 0)
    assert is_bold("Foo", 16)
    assert not is_bold("IBMPlexMono-Medium", 0)
    assert not is_bold("Foo", 0)


def test_is_italic():
    assert is_italic("Foo-Oblique", 0)
    assert is_italic("Foo-Italic", 0)
    assert is_italic("Foo", 2)
    assert not is_italic("Foo", 0)


# --- FontResolver ------------------------------------------------------------

def test_resolver_follows_type3_font_descriptor():
    resolver = FontResolver(_type3_doc())
    assert resolver.resolve("Type3 (16 0 R)") == "IBM-Plex-Sans"


def test_resolver_strips_subset_prefix():
    resolver = FontResolver(StubDoc())
    assert resolver.resolve("ABCDEF+Arial") == "Arial"
    assert resolver.resolve("Arial") == "Arial"


def test_resolver_is_type3():
    resolver = FontResolver(StubDoc())
    assert resolver.is_type3("Type3 (16 0 R)")
    assert not resolver.is_type3("Arial")
    assert not resolver.is_type3("Type3 (x 0 R)")


def test_resolver_returns_input_when_doc_raises():
    resolver = FontResolver(StubDoc(error=RuntimeError("broken xref")))
    assert resolver.resolve("Type3 (16 0 R)") == "Type3 (16 0 R)"


def test_resolver_caches_results():
    doc = _type3_doc()
    resolver = FontResolver(doc)
    assert resolver.resolve("Type3 (16 0 R)") == "IBM-Plex-Sans"
    calls = doc.calls
    assert calls > 0
    assert resolver.resolve("Type3 (16 0 R)") == "IBM-Plex-Sans"
    assert doc.calls == calls


def test_resolver_caches_failures_too():
    doc = StubDoc(error=RuntimeError("boom"))
    resolver = FontResolver(doc)
    resolver.resolve("Type3 (16 0 R)")
    calls = doc.calls
    resolver.resolve("Type3 (16 0 R)")
    assert doc.calls == calls


@pytest.mark.parametrize("descriptor", [
    ("null", "null"),
    ("dict", "<<>>"),
    ("xref", "not a ref"),
    ("xref", ""),
    None,
    "garbage",
    ("xref",),
])
def test_resolver_returns_input_for_bad_font_descriptor(descriptor):
    doc = StubDoc({(16, "FontDescriptor"): descriptor})
    assert FontResolver(doc).resolve("Type3 (16 0 R)") == "Type3 (16 0 R)"


@pytest.mark.parametrize("font_name", [
    ("null", "null"),
    ("string", "(IBM)"),
    None,
])
def test_resolver_returns_input_for_missing_or_wrong_type_font_name(font_name):
    doc = StubDoc({
        (16, "FontDescriptor"): ("xref", "601 0 R"),
        (601, "FontName"): font_name,
    })
    assert FontResolver(doc).resolve("Type3 (16 0 R)") == "Type3 (16 0 R)"


def test_resolver_does_not_query_doc_for_regular_fonts():
    doc = StubDoc()
    FontResolver(doc).resolve("Helvetica")
    assert doc.calls == 0


def test_resolver_tolerates_non_string_input():
    resolver = FontResolver(StubDoc())
    assert resolver.resolve(None) == ""
    assert not resolver.is_type3(None)


# --- Colours -----------------------------------------------------------------

def test_color_int_to_hex():
    assert color_int_to_hex(0x00897C) == "#00897c"
    assert color_int_to_hex(0) == "#000000"
    assert color_int_to_hex(0xFFFFFF) == "#ffffff"


def test_fill_to_hex():
    assert fill_to_hex((1.0, 1.0, 1.0)) == "#ffffff"
    assert fill_to_hex((0, 0.537, 0.486)) == "#00897c"
    assert fill_to_hex((0.0, 0.0, 0.0)) == "#000000"


def test_fill_to_hex_clamps_out_of_range_values():
    assert fill_to_hex((2.0, -1.0, 1.5)) == "#ff00ff"


def test_fill_to_hex_tolerates_malformed_input():
    assert fill_to_hex(()) == "#000000"
    assert fill_to_hex((1.0,)) == "#ff0000"
    assert fill_to_hex(None) == "#000000"
    assert fill_to_hex(("x", 1.0, float("nan"))) == "#00ff00"
    assert fill_to_hex((float("inf"), 0, 0)) == "#000000"


def test_is_near_black():
    assert is_near_black("#10211f")
    assert is_near_black("#404040")
    assert not is_near_black("#56635f")
    assert not is_near_black("#410000")


def test_is_light():
    assert is_light("#f3f6f5")
    assert is_light("#d0d0d0")
    assert not is_light("#00897c")
    assert not is_light("#cfffff")


def test_color_classifiers_reject_malformed_hex():
    for bad in ("", "#fff", "not a color", None):
        assert not is_near_black(bad)
        assert not is_light(bad)
