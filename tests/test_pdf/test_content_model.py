"""
Tests for the PDF data model (src/core/pdf/content.py): formats, boxes, tables.
"""

import dataclasses
import importlib
import sys

import pytest

from src.core.pdf.content import (
    FONT_FAMILIES,
    PARAGRAPH_STYLES,
    ParagraphFormat,
    PdfBox,
    PdfPlainContent,
    PdfTable,
)


class TestLegacyConstruction:
    def test_legacy_fields_only(self):
        content = PdfPlainContent(
            paragraphs_text=["Title", "Body"],
            paragraphs_style=["heading1", "normal"],
            images_by_paragraph={},
            page_size=(595.0, 842.0),
            title="T",
            author="A",
            page_count=1,
        )

        assert content.paragraphs_format == []
        assert content.boxes == []
        assert content.tables == []
        assert content.default_font_family == "serif"
        assert content.format_at(0) == ParagraphFormat()
        assert content.format_at(1) == ParagraphFormat()

    def test_legacy_positional_order_unchanged(self):
        content = PdfPlainContent(["a"], ["normal"], {}, (100.0, 200.0), "T", "A", 3)

        assert content.page_size == (100.0, 200.0)
        assert content.title == "T"
        assert content.author == "A"
        assert content.page_count == 3

    def test_default_construction(self):
        content = PdfPlainContent()

        assert content.paragraphs_format == []
        assert content.default_font_family == "serif"

    def test_new_fields_follow_legacy_fields(self):
        names = [f.name for f in dataclasses.fields(PdfPlainContent)]

        assert names == [
            "paragraphs_text", "paragraphs_style", "images_by_paragraph", "page_size",
            "title", "author", "page_count",
            "paragraphs_format", "boxes", "tables", "default_font_family",
        ]


class TestFormatAt:
    def test_returns_recorded_format(self):
        red_bold = ParagraphFormat(color="#ff0000", bold=True)
        boxed = ParagraphFormat(font_family="sans-serif", box=0)
        content = PdfPlainContent(
            paragraphs_text=["a", "b", "c"],
            paragraphs_style=["normal", "normal", "normal"],
            paragraphs_format=[red_bold, ParagraphFormat(), boxed],
            boxes=[PdfBox(background="#eeeeee", border_left="#3366cc")],
        )

        assert content.format_at(0) == red_bold
        assert content.format_at(1) == ParagraphFormat()
        assert content.format_at(2) == boxed
        assert content.format_at(2).box == 0


class TestParagraphFormat:
    def test_defaults(self):
        fmt = ParagraphFormat()

        assert fmt.color is None
        assert fmt.font_family is None
        assert fmt.bold is False
        assert fmt.italic is False
        assert fmt.box is None

    def test_equality_by_value(self):
        assert ParagraphFormat(color="#112233", italic=True) == ParagraphFormat(color="#112233", italic=True)
        assert ParagraphFormat(color="#112233") != ParagraphFormat(color="#112234")

    def test_hashable(self):
        a = ParagraphFormat(bold=True, font_family="monospace")
        b = ParagraphFormat(bold=True, font_family="monospace")

        assert hash(a) == hash(b)
        assert len({a, b, ParagraphFormat()}) == 2
        assert {a: "x"}[b] == "x"

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            ParagraphFormat().bold = True


class TestBoxAndTable:
    def test_box_defaults(self):
        box = PdfBox(background="#f0f0f0")

        assert box.border_left is None
        assert box == PdfBox(background="#f0f0f0")

    def test_table_defaults(self):
        table = PdfTable(first_index=2, rows=[[2, 3], [None, 4]])

        assert table.header is False
        assert table.column_widths == []


class TestConstants:
    def test_cell_style(self):
        assert "cell" in PARAGRAPH_STYLES

    def test_font_families(self):
        assert FONT_FAMILIES == ("serif", "sans-serif", "monospace")


class TestPackageImport:
    def test_import_without_pymupdf_exposes_new_names(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'pymupdf', None)
        monkeypatch.setitem(sys.modules, 'fitz', None)
        for name in [m for m in sys.modules if m == 'src.core.pdf' or m.startswith('src.core.pdf.')]:
            monkeypatch.delitem(sys.modules, name)

        module = importlib.import_module('src.core.pdf')

        assert module.ParagraphFormat() == module.ParagraphFormat()
        assert module.PdfBox(background="#000000").background == "#000000"
        assert module.PdfTable(first_index=0, rows=[[0]]).rows == [[0]]
        assert module.FONT_FAMILIES == ("serif", "sans-serif", "monospace")
        for name in ("ParagraphFormat", "PdfBox", "PdfTable", "FONT_FAMILIES"):
            assert name in module.__all__
