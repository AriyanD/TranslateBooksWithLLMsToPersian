# BLUEPRINT — PDF formatting fidelity, track A (reflowed mode)

## Summary

Improve the **reflowed** PDF output shipped by `blueprint/BLUEPRINT_PdfSupport.md` (branch
`feat/pdf-support`, package `src/core/pdf/`) so a translated PDF keeps far more of its source
formatting, without changing the approach (we still rebuild a new PDF from translated paragraphs;
in-place layout preservation is track B, out of scope).

Five additions, agreed with the user (2026-10-01):

| # | Feature | Result in the output |
|---|---|---|
| 1 | **Tables** | Ruled tables become real HTML tables; every non-empty cell is one translation unit. |
| 2 | **Heading split** | A text block whose lines change font size (or uniform colour) is split, so eyebrow / title / subtitle each get their own style. |
| 3 | **Paragraph colour** | Dominant text colour per paragraph (near-black and unreadable light colours skipped). |
| 4 | **Background boxes** | A filled rectangle behind paragraphs becomes a box with its background colour, plus a left bar when the source has one. |
| 5 | **Font family + paragraph-level bold/italic** | serif / sans-serif / monospace per paragraph, resolved from real font names (including Type3 fonts through their `FontDescriptor`). A paragraph whose spans are *all* bold (or all italic) is rendered bold (italic). |

Out of scope: intra-sentence bold/italic/colour (the plain-text pipeline carries no markup),
in-place layout (track B), gradients, background images, badge backgrounds inside table cells,
column spans, OCR.

### Facts verified during planning (PyMuPDF 1.28.2, reference document + probes)

The reference document is a 3-page French HR note printed by headless Chrome (Skia/PDF). It is a
private file: **it must never be committed or used as a test fixture**. Synthetic fixtures
reproduce its structure.

1. `page.find_tables()` (default `lines` strategy) finds **only the header rows** of its tables
   (`row_count == 1`): Chrome draws per-cell `border-bottom` as thin **filled rectangles, one per
   column segment** (e.g. `[45.8,101.2]`, `[101.2,366.0]`, `[366.0,549.8]` at each row boundary),
   and no vertical lines. The `text` strategies return garbage (10 columns, 69 rows).
2. `page.find_tables(clip=…, vertical_strategy="explicit", horizontal_strategy="explicit",
   vertical_lines=[45.8, 101.2, 366.0, 549.8], horizontal_lines=[390.8, 411.0, 477.8, 530.2, 597.0,
   649.5, 702.0])` returns the **exact** table (6 rows × 3 columns, correct cell texts, multi-line
   cells joined with `"\n"`).
3. Header rows are drawn as one non-white filled rectangle per column cell (`#f3f6f5`) whose bottom
   edge touches the first rule. Page 3 holds a **continuation** table with no header fill: its first
   row sits *above* the first rule.
4. Callout box = a filled rect (`#f3f6f5`, 504×97 pt) plus a 2.2 pt wide filled rect at its left edge
   (`#9a5b2d`). A full-page white filled rect (`#ffffff`) also exists and must be ignored.
5. Type3 span font names look like `"Type3 (16 0 R)"`. `doc.xref_get_key(16, "FontDescriptor")`
   → `('xref', '601 0 R')`, `doc.xref_get_key(601, "FontName")` → `('name', '/HAAAAA+IBM-Plex-Sans')`;
   bold faces are named `…IBM-Plex-Sans-Bold`. Span `flags` carry **no** bold bit for these fonts.
6. `pymupdf.Story` honours inline `style` with `color`, `font-family: serif|sans-serif|monospace`,
   `font-weight`, `background-color` and `border-left` on `<div>`, and `<table>` with per-row
   `background-color` and cell `border-bottom`. Column widths: **percent widths are ignored**
   (columns come out equal), **absolute `width:Npt` on `<td>` is honoured**. `dir="rtl"` on a `<p>`
   inside a `<td>` right-aligns.

---

## Project grounding

### Stack

Python 3.11 (Flask + Flask-SocketIO web app, argparse CLI), PyMuPDF (`pymupdf>=1.26.0`, AGPL-3.0),
pytest. PDF pipeline: `src/core/pdf/` — `content.py` (data model), `extractor.py` (PDF →
paragraphs), `builder.py` (paragraphs → reflowed PDF via `Story`/`DocumentWriter`), `translator.py`
(wires extractor → `translate_paragraphs_plain` → builder, with checkpoint/resume).

### Conventions that constrain this change (`CLAUDE.md`)

1. **English only** for everything committed (code, identifiers, comments, docstrings, docs,
   test names, commit messages).
2. **No secrets** anywhere.
3. **No ad-hoc scripts at the repo root.** Tests under `tests/` (pytest).
4. Frontend i18n rules: **not touched** by this blueprint (no UI change).
5. Mirror the existing `src/core/pdf/` style: module-level named constants, small private helpers
   per step, English docstrings, `logging.getLogger(__name__)`, no prints.
6. **No binary fixtures**: every test PDF is generated with PyMuPDF at test time (existing
   `tests/test_pdf/conftest.py` pattern). The user's reference document is never committed.
7. **Colour assertions in tests** compare per channel with a tolerance of ±1 (PDF colour values
   are stored as floats and round-trip with small rounding differences). Hex values quoted in the
   validation criteria are the expected values within that tolerance.

### Verification gate (quote verbatim to every sub-agent)

Bare `python` on this machine resolves to the wrong interpreter. Always use the project venv:

```
./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q
./venv/Scripts/python.exe -m pytest -q
```

Baseline before this blueprint: `tests/unit tests/test_pdf` = **1936 passed, 1 skipped**. The full
run has **five pre-existing failures** in `tests/characterization/test_progress_baseline.py`
(`test_translate_progress_baseline[docx|epub]`, `test_refine_progress_baseline[docx]`,
`test_translate_with_inline_refine_baseline[docx|epub]`): stale goldens, not regressions. A phase is
green when it adds no failure outside that list. No CI job runs pytest.

### Sacred / high-risk surfaces involved

| Surface | Why | Phases |
|---|---|---|
| **Extractor ↔ builder seam** (`src/core/pdf/content.py`) | Shared data model; every PDF consumer reads it. Must stay backward compatible (new fields with defaults). | 1 |
| **Checkpoint / resume — unit list determinism** | `paragraphs_text` *is* the persisted translation unit list (`paragraph_count` is stored in the plain-text partial state and checked by `resume_plain_segments`). The extraction must stay deterministic for a given input. No schema change. | 4 |
| **Untrusted input parsing** | New PyMuPDF calls (`find_tables`, `get_drawings`, `xref_get_key`) on user files. They must never make extraction fail. | 2, 3, 4 |

Not touched: prompts (`src/prompts/`), `plain_text_pipeline.py`, `plain_text_checkpoint.py`,
upload security, routing (`translate_file.py`), web UI, locales.

---

## Architecture / integration notes

### Data flow (changes in **bold**)

```
input.pdf
  └─ extract_pdf_paragraphs(path)                          src/core/pdf/extractor.py
        ├─ FontResolver / classify_family / is_bold / colours   **NEW src/core/pdf/styling.py**
        ├─ detect_tables(page, line_bboxes) / detect_boxes      **NEW src/core/pdf/layout.py**
        → PdfPlainContent(paragraphs_text, paragraphs_style,
                          **paragraphs_format, boxes, tables, default_font_family**, …)
  └─ translate_paragraphs_plain(content.paragraphs_text)   unchanged (cells are ordinary units)
  └─ build_pdf(translated, content, …)                     src/core/pdf/builder.py (**formats, boxes, tables**)
```

Table cells are **ordinary translation units**: they live in `paragraphs_text` with style `"cell"`,
contiguous and in row-major order; `PdfTable` only records the grid of indices. Hence
`translator.py`, the plain-text pipeline and the checkpoint machinery need no structural change.

### Integration points (exact contracts)

| File | Symbol | Contract |
|---|---|---|
| `src/core/pdf/content.py` | `PARAGRAPH_STYLES = ("heading1", "heading2", "heading3", "list", "normal")`; `PdfImage(data, ext, width_pt, height_pt)`; `PdfPlainContent(paragraphs_text, paragraphs_style, images_by_paragraph, page_size, title, author, page_count)`; exceptions `PdfError`, `PdfOpenError`, `PdfEncryptedError`, `PdfNoTextLayerError`, `PdfBuildError` | Extended in Phase 1 (new fields with defaults, `"cell"` style). Pure data, no pymupdf import. |
| `src/core/pdf/extractor.py` | `extract_pdf_paragraphs(source: Union[str, bytes], *, include_images: bool = True) -> PdfPlainContent`; `extract_pdf_text(source, hard_cap: Optional[int] = None) -> str` (never raises); `join_lines(prev, nxt) -> str`; private `_normalize_text`, `_join_and_normalize`, `_TextBlock(page_index, page_height, block, lines, text)`, `_collect_blocks`, `_drop_headers_footers`, `_dominant_size(spans)`, `_block_style(item, body_size)`, `_split_list_items(lines)`, `_should_merge(prev_text, prev_style, text)`, `_ParagraphSink.append(text, style)`, `_extract(doc, include_images)` | Phase 4 adds the keyword `detect_layout: bool = True` and the steps below. Constants already in the module (`HEADER_FOOTER_BAND`, `H1_RATIO`, `H2_RATIO`, `H3_RATIO`, `BOLD_FLAG = 16`, `TEXT_FLAGS`, …) are kept. |
| `src/core/pdf/__init__.py` | Lazy wrappers `extract_pdf_paragraphs(source, *, include_images=True)`, `extract_pdf_text(source, hard_cap=None)`, `build_pdf(...)`, `translate_pdf_file(...)` | Phase 4 adds `detect_layout` pass-through to the `extract_pdf_paragraphs` wrapper. Package import must keep working without pymupdf. |
| `src/core/pdf/builder.py` | `build_pdf(translated_paragraphs: List[str], content: PdfPlainContent, output_path: str, target_language: str, source_language: str = "", bilingual: bool = False) -> None`; private `_text_element(text, style, rtl, source)`, `_build_html(...) -> (html, archive)`, `_render_story(...)`, `_save_with_metadata(...)`, constant `_PDF_CSS` | Signature unchanged. Phase 5 adds format/box/table rendering. Error contract unchanged: only `ValueError` or `PdfBuildError`. |
| `src/core/pdf/translator.py` | `translate_pdf_file(...)` logs `log_callback("pdf_extracted", f"📄 PDF: {pages} pages, {n} paragraphs, {n_images} images")` | Phase 6 adds the table count to that message (same key). |
| `src/core/common/plain_text_checkpoint.py` | `resume_plain_segments(resume_state, paragraph_count, log_callback)` returns `(None, None)` when the stored `paragraph_count` differs | **Not modified.** Consequence: a PDF job checkpointed by the pre-track-A extractor restarts from scratch if its unit count changed. Acceptable: PDF support is unreleased (branch `feat/pdf-support`). |
| PyMuPDF | `page.find_tables(clip=None, strategy=None, vertical_strategy=…, horizontal_strategy=…, vertical_lines=…, horizontal_lines=…)` → `TableFinder.tables: List[Table]`; `Table.bbox`, `Table.row_count`, `Table.col_count`, `Table.rows[r].cells[c]` (bbox tuple or `None`), `Table.extract() -> List[List[Optional[str]]]`; `page.get_drawings()` → dicts with `fill` (RGB floats or `None`), `fill_opacity`, `color`, `width`, `rect`, `items` (`("re", Rect, …)`, `("l", Point, Point)`, …); `doc.xref_get_key(xref, key) -> (type, value)`; span flags: 2 italic, 4 serifed, 8 monospaced, 16 bold; span `color` is an sRGB int | Used by Phases 2 and 3 only. |

---

## Phased breakdown

Dependency graph:

```
Phase 1 (data model)
   ├── Phase 2 (styling helpers)  ─┐  2 and 3 in parallel
   ├── Phase 3 (layout detection) ─┤
   │                               └── Phase 4 (extractor integration)
   └── Phase 5 (builder) ─────────────────────────────┐  5 runs in parallel with 2, 3 and 4
                                                       └── Phase 6 (translator log, round-trip test, docs)
                                    Phase 4 ──────────┘
```

File ownership (no two parallel phases share a file):
- Phase 2: `styling.py`, `tests/test_pdf/test_styling.py`
- Phase 3: `layout.py`, `tests/test_pdf/test_layout.py`, **`tests/test_pdf/conftest.py`** (adds fixtures)
- Phase 4: `extractor.py`, `__init__.py`, `tests/test_pdf/test_extractor_formatting.py`
- Phase 5: `builder.py`, `tests/test_pdf/test_builder_formatting.py`
- Phase 6: `translator.py`, `tests/test_pdf/test_formatting_roundtrip.py`, `README.md`, `docs/CLI.md`, `docs/BACKLOG.md`

---

### Phase 1 — Data model extension

**Goal.** Extend `content.py` with the per-paragraph format, boxes and tables, backward compatible.

**Files touched.** `src/core/pdf/content.py` (modify), `src/core/pdf/__init__.py` (modify: re-export the new names), `tests/test_pdf/test_content_model.py` (create).

**Concrete deliverables.**

```python
PARAGRAPH_STYLES = ("heading1", "heading2", "heading3", "list", "normal", "cell")
FONT_FAMILIES = ("serif", "sans-serif", "monospace")

@dataclass(frozen=True)
class ParagraphFormat:
    """Visual attributes of one paragraph (all optional; defaults = plain paragraph)."""
    color: Optional[str] = None        # "#rrggbb" (lowercase) or None = default text colour
    font_family: Optional[str] = None  # one of FONT_FAMILIES, or None = document default
    bold: bool = False                 # every non-blank span of the paragraph is bold
    italic: bool = False               # every non-blank span of the paragraph is italic
    box: Optional[int] = None          # index into PdfPlainContent.boxes, or None

@dataclass(frozen=True)
class PdfBox:
    """A filled rectangle drawn behind one or more paragraphs (or a table header row)."""
    background: str                    # "#rrggbb"
    border_left: Optional[str] = None  # "#rrggbb" of a thin bar on the box's left edge, or None

@dataclass
class PdfTable:
    """A table whose non-empty cells are contiguous units of paragraphs_text."""
    first_index: int                   # index in paragraphs_text of the first non-empty cell
    rows: List[List[Optional[int]]]    # paragraph index per cell (row-major), None = empty cell
    header: bool = False               # rows[0] is a header row
    column_widths: List[float] = field(default_factory=list)  # relative widths, sum == 1.0
```

`PdfPlainContent` gains, after the existing fields (existing fields and order unchanged):

```python
    paragraphs_format: List[ParagraphFormat] = field(default_factory=list)  # parallel to paragraphs_text, or empty
    boxes: List[PdfBox] = field(default_factory=list)
    tables: List[PdfTable] = field(default_factory=list)
    default_font_family: str = "serif"   # one of FONT_FAMILIES
```

Add `def format_at(self, index: int) -> ParagraphFormat` on `PdfPlainContent`: returns
`paragraphs_format[index]` when `paragraphs_format` is non-empty, else `ParagraphFormat()`.

**Invariants (documented in the docstrings):**
- `paragraphs_format` is either empty (legacy / no formatting) or `len == len(paragraphs_text)`.
- For each `PdfTable t`: the non-`None` entries of `t.rows`, read row-major, are exactly
  `range(t.first_index, t.first_index + n)` with `n >= 1`; each such index has style `"cell"`;
  every row has the same length; `len(t.column_widths) == len(t.rows[0])`.
- Tables are listed by increasing `first_index`; their index ranges never overlap.
- Every `ParagraphFormat.box` is a valid index into `boxes`.

Re-export from `src/core/pdf/__init__.py`: `ParagraphFormat`, `PdfBox`, `PdfTable`, `FONT_FAMILIES` (still no pymupdf import).

**Dependency.** Sequential (first).

**Risk surface.** Extractor ↔ builder seam.

**Contract.** `PdfPlainContent(...)` built with only the legacy fields still works and `format_at(i)` returns `ParagraphFormat()`. `ParagraphFormat` is hashable and compares by value. No pymupdf import in `content.py`.

**Validation criteria.**
- `tests/test_pdf/test_content_model.py`: legacy construction; `format_at` with empty and non-empty `paragraphs_format`; `ParagraphFormat` equality/hash; `"cell"` in `PARAGRAPH_STYLES`; `import src.core.pdf` with `sys.modules['pymupdf'] = None` still succeeds (mirror the existing test in `test_file_detection.py`).
- All existing `tests/test_pdf` tests still pass unchanged.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 2 — Styling helpers (fonts, colours)

**Goal.** Pure, never-raising helpers that turn PyMuPDF span data into font family, bold/italic and colour values.

**Files touched.** `src/core/pdf/styling.py` (create), `tests/test_pdf/test_styling.py` (create).

**Concrete deliverables.**

```python
TYPE3_NAME_RE = re.compile(r"^Type3 \((\d+) 0 R\)$")
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
ITALIC_FLAG, SERIF_FLAG, MONO_FLAG, BOLD_FLAG = 2, 4, 8, 16
NEAR_BLACK_MAX = 0x40          # every channel <= this -> treated as default text colour
LIGHT_MIN = 0xD0               # every channel >= this -> light colour
MONO_TOKENS = ("mono", "courier", "consol", "menlo", "inconsolata", "code")
SANS_TOKENS = ("sans", "helvetica", "arial", "verdana", "tahoma", "segoe", "calibri",
               "roboto", "inter", "lato", "frutiger", "futura", "gothic", "nimbussans")
SERIF_TOKENS = ("serif", "times", "roman", "georgia", "garamond", "minion", "palatino",
                "cambria", "baskerville", "charis", "mincho", "song", "ming")
BOLD_TOKENS = ("bold", "black", "heavy")
ITALIC_TOKENS = ("italic", "oblique")

class FontResolver:
    """Resolves span font names to real font names, with a per-document cache."""
    def __init__(self, doc) -> None
    def resolve(self, span_font: str) -> str
    def is_type3(self, span_font: str) -> bool        # TYPE3_NAME_RE.match(span_font) is not None

def normalize_font_name(name: str) -> str              # lower-case, keep [a-z0-9] only
def classify_family(font_name: str, flags: int, *, use_flags: bool = True) -> Optional[str]
def is_bold(font_name: str, flags: int) -> bool
def is_italic(font_name: str, flags: int) -> bool
def color_int_to_hex(color: int) -> str                # 0x00897c -> "#00897c"
def fill_to_hex(fill: Sequence[float]) -> str          # (0, 0.537, 0.486) -> "#00897c" (round(c * 255), clamped 0..255)
def is_near_black(hex_color: str) -> bool
def is_light(hex_color: str) -> bool
```

Normative behaviour:
1. `FontResolver.resolve(span_font)`: if `TYPE3_NAME_RE` matches, read `doc.xref_get_key(xref, "FontDescriptor")`; when it returns `("xref", "<n> 0 R")`, read `doc.xref_get_key(n, "FontName")`; when that returns `("name", "/X")`, the result is `X` without the leading `/`. Otherwise (no match, missing key, any exception) the result is `span_font`. In all cases strip `SUBSET_PREFIX_RE`. Results cached by `span_font`. Never raises.
2. `classify_family(name, flags, use_flags=True)`: `n = normalize_font_name(name)`; first token match wins in this order: any `MONO_TOKENS` in `n` → `"monospace"`; any `SANS_TOKENS` → `"sans-serif"`; any `SERIF_TOKENS` → `"serif"` (sans is tested before serif so `"sansserif"` is sans). No token match: if `use_flags` → `flags & MONO_FLAG` → `"monospace"`, `flags & SERIF_FLAG` → `"serif"`, else `None`; if not `use_flags` → `None`. The extractor passes `use_flags=False` for an unresolved Type3 name (its flags are meaningless).
3. `is_bold`: `bool(flags & BOLD_FLAG)` or any `BOLD_TOKENS` in `normalize_font_name(name)` (`"semibold"`, `"extrabold"` match via `"bold"`). `is_italic`: `flags & ITALIC_FLAG` or any `ITALIC_TOKENS`.
4. Colour helpers: lowercase hex, 7 chars. `is_near_black`: all three channels `<= NEAR_BLACK_MAX`. `is_light`: all three channels `>= LIGHT_MIN`.

**Dependency.** Parallelizable (with Phase 3; needs no other phase).

**Risk surface.** Untrusted input parsing (`xref_get_key` on user PDFs): must never raise.

**Contract.** Pure functions, deterministic, no I/O except `FontResolver` reading the given `doc`.

**Validation criteria** (`tests/test_pdf/test_styling.py`):
1. `classify_family`: `"IBM-Plex-Sans"` → sans-serif; `"IBMPlexMono-Medium"` → monospace; `"DejaVuSansMono"` → monospace; `"NotoSerif-Regular"` → serif; `"Times-Roman"` → serif; `"Helvetica-Bold"` → sans-serif; `"MS Gothic"` → sans-serif; `"XyzUnknown"` with flags 4 → serif, flags 8 → monospace, flags 0 → None; `"XyzUnknown"`, flags 4, `use_flags=False` → None.
2. `is_bold("IBM-Plex-Sans-Bold", 0)`, `is_bold("Foo-SemiBold", 0)`, `is_bold("Foo", 16)` are True; `is_bold("IBMPlexMono-Medium", 0)` False. `is_italic("Foo-Oblique", 0)` True.
3. `FontResolver` on a stub doc (`xref_get_key` returning the tuples listed in fact 5): `"Type3 (16 0 R)"` → `"IBM-Plex-Sans"`; `"ABCDEF+Arial"` → `"Arial"`; a stub raising on `xref_get_key` → returns the input unchanged; second call served from cache (stub call count unchanged).
4. Colour helpers: `color_int_to_hex(0x00897C) == "#00897c"`, `fill_to_hex((1.0, 1.0, 1.0)) == "#ffffff"`, `is_near_black("#10211f")` True, `is_near_black("#56635f")` False, `is_light("#f3f6f5")` True, `is_light("#00897c")` False.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 3 — Layout detection (tables, boxes) and fixtures

**Goal.** Detect ruled tables (including Chrome-style "horizontal rules only" tables) and filled background boxes on a page, plus the synthetic fixtures every later phase uses.

**Files touched.** `src/core/pdf/layout.py` (create), `tests/test_pdf/test_layout.py` (create), `tests/test_pdf/conftest.py` (modify: add fixtures; existing fixtures unchanged).

**Concrete deliverables.**

Constants (module level, exact):

```python
RULE_MAX_THICKNESS = 2.0       # pt: a filled rect at most this tall is a horizontal rule
RULE_MIN_LENGTH = 10.0         # pt
RULE_Y_TOLERANCE = 1.0         # pt: rules closer than this belong to the same separator row
BOUNDARY_TOLERANCE = 1.5       # pt: column boundaries closer than this are the same boundary
MAX_ROW_HEIGHT_RATIO = 0.25    # max distance between consecutive separators, fraction of page height
HEADER_COVERAGE = 0.9          # header fills must cover this share of the grid width
MIN_BOX_SIDE = 4.0             # pt
WHITE_MIN = 0.97               # every fill channel >= this -> page background, ignored
MAX_BOX_AREA_RATIO = 0.5       # boxes larger than this share of the page are backgrounds, ignored
BAR_MAX_WIDTH = 6.0            # pt
BAR_EDGE_TOLERANCE = 3.0       # pt
CONTAIN_TOLERANCE = 2.0        # pt
```

Data classes (in `layout.py`, internal to the PDF package):

```python
@dataclass
class DetectedTable:
    bbox: Tuple[float, float, float, float]
    cells: List[List[Optional[str]]]                                # Table.extract() output
    cell_bboxes: List[List[Optional[Tuple[float, float, float, float]]]]
    header: bool
    header_fill: Optional[str]                                      # "#rrggbb" when header is True
    column_widths: List[float]                                      # relative, sum 1.0

@dataclass
class DetectedBox:
    rect: Tuple[float, float, float, float]
    background: str                                                 # "#rrggbb"
    border_left: Optional[str]
```

Public functions (both **never raise**: any exception → log at debug level and return `[]`):

```python
def detect_tables(page, line_bboxes: List[Tuple[float, float, float, float]]) -> List[DetectedTable]
def detect_boxes(page, exclude: List[Tuple[float, float, float, float]]) -> List[DetectedBox]
```

`detect_tables` algorithm (normative):
1. **Fully ruled tables.** `page.find_tables()`; keep tables with `row_count >= 2` and `col_count >= 2`. Header: `True` when the non-white fills (see step 3 definition of "fill rects") inside the bbox of row 0 cover `>= HEADER_COVERAGE` of row 0's width; `header_fill` = hex of the fill covering the most width. `column_widths` from the cells of the row with the most non-`None` cells.
2. **Separator rows.** From `page.get_drawings()`: (a) each `"re"` item of a path with `fill is not None` whose rect has `height <= RULE_MAX_THICKNESS` and `width >= RULE_MIN_LENGTH` gives a segment `(x0, x1, y=(y0+y1)/2)`; (b) each `"l"` item of a path with `color is not None` and `fill is None` whose two points have `|dy| <= 0.5` and `|dx| >= RULE_MIN_LENGTH` gives a segment. Sort segments by `y`, group them (a new group starts when `y - first_y_of_group > RULE_Y_TOLERANCE`). A group's **signature** is the sorted list of all segment endpoints, values closer than `BOUNDARY_TOLERANCE` to the previous kept value dropped. Groups with fewer than 3 boundaries (fewer than 2 columns) are ignored. The group's `y` is the mean of its segments' `y`.
3. **Fill rects** = `"re"` items of paths with `fill is not None`, `fill_opacity` (default 1.0) `>= 0.5`, `height > RULE_MAX_THICKNESS`, not white (every channel `>= WHITE_MIN` → ignored).
4. **Grids.** Walk separator groups by increasing `y`. A run starts at any group; the next group extends the run when its signature has the same length and every boundary is within `BOUNDARY_TOLERANCE` of the run's first signature, and `next.y - previous.y <= MAX_ROW_HEIGHT_RATIO * page.rect.height`; otherwise the current run closes and a new run starts at that group.
5. **Top of a run.** Header fills = fill rects with `|rect.y1 - first.y| <= RULE_Y_TOLERANCE + 1.0` and `first.y - rect.y0 <= MAX_ROW_HEIGHT_RATIO * page height`, lying within the run's x-range ±`BOUNDARY_TOLERANCE`. If their union covers `>= HEADER_COVERAGE` of the run's width → `header = True`, `top = min(rect.y0)`, `header_fill` = hex of the widest header fill. Otherwise `header = False`; `h_max` = the largest gap between consecutive separators of the run (or `0.05 * page height` for a single-separator run); candidate lines = `line_bboxes` whose x-centre lies in the run's x-range and with `y1 <= first.y + 1` and `y0 >= first.y - h_max`; `top = min(y0) - 1` of the candidates, or `first.y` when there are none.
6. **Validity.** Keep a run when (`header` and it has `>= 1` separator) or it has `>= 2` separators, and the resulting rows (boundaries `[top] + separator ys`, deduplicated) number `>= 1`.
7. **Extraction.** `page.find_tables(clip=Rect(x0 - 1, top - 1, x1 + 1, last_y + 1), vertical_strategy="explicit", horizontal_strategy="explicit", vertical_lines=<signature>, horizontal_lines=<[top] + separator ys>)`; take `tables[0]` (no table → drop the run). `cells = table.extract()`, `cell_bboxes = [row.cells for row in table.rows]`, `column_widths` from the signature.
8. **Dedup.** Drop any grid whose bbox intersects (positive area) a table kept in step 1. Return step-1 tables then grids, sorted by `(bbox.y0, bbox.x0)`.

`detect_boxes` algorithm (normative):
1. Candidates = fill rects (step 3 above) with `width >= MIN_BOX_SIDE`, `height >= MIN_BOX_SIDE`, area `<= MAX_BOX_AREA_RATIO * page area`, and not contained (±`CONTAIN_TOLERANCE`) in any `exclude` rect.
2. Bars = `"re"` fill items (any colour except white) with `width <= BAR_MAX_WIDTH` and `height >= MIN_BOX_SIDE`.
3. For each candidate: `border_left` = colour of the first bar (in drawing order) with `|bar.x0 - box.x0| <= BAR_EDGE_TOLERANCE`, `bar.height >= 0.5 * box.height` and vertical overlap `>= 0.8 * box.height`; else `None`.
4. Deduplicate identical rects (rounded to 0.1 pt; keep the first). Return sorted by `(area, y0, x0)` ascending.

Fixtures added to `tests/test_pdf/conftest.py` (generated with PyMuPDF, 595×842 pages, returning `str` paths; reuse the module's existing helpers `_pymupdf`, `_new_page`, `_png_bytes`, `_save`):
- `formatted_pdf_path`, page 1:
  - **Title block**: three lines that PyMuPDF must return as **one** text block: eyebrow `"ACME - INTERNAL NOTE"` (Courier `"cour"`, 8 pt, colour `(0, 0.537, 0.486)`), title `"Legal framework of the review"` (`"hebo"`, 21 pt, black), subtitle `"Texts that apply to the weekly review tool."` (`"helv"`, 10 pt, colour `(0.337, 0.388, 0.373)`). Start with baselines y = 60 / 82 / 98; the fixture's own sanity test (in `test_layout.py`) asserts the three lines share one block, and the implementer tightens the spacing until it holds.
  - **Callout**: `page.draw_rect(Rect(46, 250, 550, 320), color=None, fill=(0.953, 0.965, 0.961))`, bar `page.draw_rect(Rect(46, 250, 48.2, 320), color=None, fill=(0.604, 0.357, 0.176))`, two `"helv"` 10 pt paragraphs inside it (≥ 30 pt apart).
  - **Chrome-style table**: columns `[46, 101, 366, 550]`; header fills per column cell `Rect(x0, 391, x1, 411.8)` fill `(0.953, 0.965, 0.961)`; per-column thin rules (height 0.8, fill `(0.851, 0.878, 0.871)`) at y = 411, 478, 530; header labels `"ARTICLE"`, `"OBLIGATION"`, `"APPLICATION"`; data rows: col 0 `"L1222-3"` / `"L1222-4"` in `"cour"`; cols 1–2 one or two `"helv"` 9 pt lines each (one cell of row 2 left empty).
  - A `"hebo"` 13 pt heading `"2 Second section"` below the table, then a normal body paragraph.
- `formatted_pdf_path`, page 2: a **continuation table** with the same columns, no header fill, first row text above the first rule (rules at y = 120, 170), then a body paragraph.
- `ruled_table_pdf_path`: one page with a 3×3 table fully stroked (`page.draw_rect(cell, color=(0, 0, 0), width=0.5)` per cell), one word per cell, plus one body paragraph above it.

**Dependency.** Parallelizable (with Phase 2; needs no other phase).

**Risk surface.** Untrusted input parsing (`find_tables`, `get_drawings`): must never raise.

**Contract.** Deterministic for a given page. `detect_tables` returns tables whose `cells` and `cell_bboxes` have identical shapes and whose `column_widths` sum to 1.0 (±1e-6). `detect_boxes` never returns a rect that is white, rule-thin, page-sized or inside an `exclude` rect.

**Validation criteria** (`tests/test_pdf/test_layout.py`):
1. `formatted_pdf_path` page 1 sanity: the title block's three lines share one block.
2. Page 1 → exactly one table; `header is True`; `header_fill == "#f3f6f5"`; `len(cells) == 3` (header + 2 data rows), 3 columns; `cells[1][0] == "L1222-3"`; one empty cell (`None` or `""`); `column_widths ≈ [55/504, 265/504, 184/504]` (±0.01).
3. Page 2 → one table, `header is False`, its first row contains the text placed above the first rule.
4. `ruled_table_pdf_path` → one table (step 1 path), 3×3, `header is False`.
5. `detect_boxes(page1, [table.bbox])` → one box `(46, 250, 550, 320)`, `background == "#f3f6f5"`, `border_left == "#9a5b2d"`; no box for the white page-sized rect, the header fills or the rules.
6. `detect_tables`/`detect_boxes` on a page object whose `find_tables`/`get_drawings` raise → `[]` (use a stub object).
7. Pages of the existing `simple_pdf_path` fixture → no table, no box.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE. Known accepted limits (documented in the module docstring): tables drawn with neither vertical lines nor per-column rules, "border-top" tables whose last row is below the last rule, column spans, and badge fills inside cells are not reproduced.

---

### Phase 4 — Extractor integration

**Goal.** Make `extract_pdf_paragraphs` emit heading splits, table cells, per-paragraph formats, boxes and the document's default font family.

**Files touched.** `src/core/pdf/extractor.py` (modify), `src/core/pdf/__init__.py` (modify: `detect_layout` pass-through), `tests/test_pdf/test_extractor_formatting.py` (create).

**Concrete deliverables.**

New signature: `extract_pdf_paragraphs(source, *, include_images: bool = True, detect_layout: bool = True) -> PdfPlainContent`. `extract_pdf_text` calls it with `include_images=False, detect_layout=False` (no table/box detection for auxiliary features; their behaviour stays as today apart from heading splits).

New module constant: `SIZE_SPLIT_RATIO = 1.15` and `COLOR_SPLIT_SHARE = 0.8`.

Algorithm changes (normative; numbering follows the existing steps of `BLUEPRINT_PdfSupport.md` Phase 2):

- **Step 2 (collect), per page, when `detect_layout`:**
  1. `line_bboxes` = bbox of every non-blank line of every text block of the page.
  2. `tables = detect_tables(page, line_bboxes)`; `boxes = detect_boxes(page, [t.bbox for t in tables])`.
  3. For each text block, remove every line whose bbox centre lies inside a table bbox (±1 pt). Those lines are kept aside on the table they belong to (`table_lines`, for cell formats). A block with no line left is dropped; otherwise its bbox becomes the union of its remaining lines.
  4. Each table becomes a `_TableItem(page_index, table, table_lines)` inserted in the page's item list at the position of the first text block (stream order) that lost at least one line to it; if none, before the first item of the page whose bbox `y0 >= table.bbox.y1`; else at the end of the page's items. Tables are placed in `(bbox.y0, bbox.x0)` order.
  5. `_TextBlock` keeps the line dicts of its remaining lines (needed for splits and formats).
- **Step 4 (header/footer)** is unchanged and runs on whole blocks (before splitting). Table items are never dropped by it.
- **Step 4b (split, NEW)** after step 4: for each kept text block, per non-blank line compute the line's dominant span size (char-weighted, rounded to 0.5 pt, same rule as `_dominant_size`) and dominant colour with its character share. Start a new sub-block before line `k >= 1` when `max(s[k-1], s[k]) / min(s[k-1], s[k]) >= SIZE_SPLIT_RATIO` (both sizes > 0), or when the dominant colours differ and both shares are `>= COLOR_SPLIT_SHARE`. Each sub-block is a `_TextBlock` with its own lines and normalised text (empty sub-blocks dropped). Every later step works on sub-blocks.
- **Step 5 (body size / scanned check):** `body_size` unchanged (text sub-blocks only). The scanned check counts non-whitespace characters of text sub-blocks **plus** all table cell texts.
- **Step 6 (style):** `all_bold` now uses `styling.is_bold(resolver.resolve(span["font"]), span["flags"])` for every non-blank span (`resolver = FontResolver(doc)`, one per document). The rest of the rule is unchanged.
- **Step 6b (format, NEW)** for every emitted paragraph, from the spans of the lines it was built from (list-split paragraphs use their own lines):
  - `color`: char-weighted dominant span colour (ties → smaller int) → hex; `None` when `is_near_black`.
  - `font_family`: char-weighted dominant non-`None` value of `classify_family(resolver.resolve(font), flags, use_flags=not resolver.is_type3(font))` (a resolved Type3 name still classifies through its name tokens; only its meaningless flags are ignored) (ties → order of `FONT_FAMILIES`); `None` when no span has a known family.
  - `bold` / `italic`: every non-blank span `is_bold` / `is_italic`.
  - `box`: the first `DetectedBox` of the same page (they are sorted smallest first) whose rect contains the union bbox of the paragraph's lines (±`CONTAIN_TOLERANCE`); registered in `content.boxes` as `PdfBox(background, border_left)`, deduplicated by `(page_index, rect)` so all paragraphs of one box share one index.
  - **Light-colour rule:** if `color` is light (`is_light`) and (`box is None` or the box background `is_light`) → `color = None`.
- **Step 7 (list split)** unchanged, applied to `normal` sub-blocks.
- **Step 8 (merge):** in addition to the existing conditions, `P` is merged into `Q` only when `P.format == Q.format`. Cells never merge and are never merged into (they are not `normal`).
- **Tables → units (NEW)** when a `_TableItem` is met: for each cell row-major, `raw = cells[r][c]`; `text = _join_and_normalize([l for l in (raw or "").split("\n") if l.strip()])`. Empty text → `None` in `PdfTable.rows`; otherwise append to the sink **without merge** with style `"cell"` and a format computed as in step 6b from the `table_lines` spans whose bbox centre lies in `cell_bboxes[r][c]`; for header cells (`header and r == 0`) the box is a registered `PdfBox(background=header_fill)`; for other cells `box = None` (then the light-colour rule drops white text). A table with no non-empty cell is skipped entirely. `column_widths` and `header` are copied from the `DetectedTable`.
- **Images**: unchanged anchoring (`len(paragraphs) - 1` when met).
- **Metadata:** `default_font_family` = char-weighted dominant non-`None` family over the spans of all `normal` and `list` paragraphs; `"serif"` when none is known.
- `paragraphs_format` is always filled (parallel), also when `detect_layout=False` (then every `box` is `None` and `tables` is empty).

Exception contract unchanged: only `PdfOpenError`, `PdfEncryptedError`, `PdfNoTextLayerError` (and `ImportError`) escape; layout helpers already never raise.

**Dependency.** Sequential (after Phases 1, 2, 3).

**Risk surface.** Checkpoint / resume — unit list determinism (`paragraphs_text` is the persisted unit list); untrusted input parsing.

**Contract.**
- Every invariant of `BLUEPRINT_PdfSupport.md` Phase 2 still holds (no `"\n"`, no empty paragraph, parallel lists, image keys in range, deterministic output, path and bytes inputs identical).
- Every invariant of Phase 1 of this blueprint holds (`paragraphs_format` parallel; table index ranges contiguous, row-major, style `"cell"`; valid box indices).
- On a PDF with no tables, boxes, colour or size changes, `paragraphs_text` and `paragraphs_style` are identical to the pre-change extractor output.

**Validation criteria** (`tests/test_pdf/test_extractor_formatting.py`, using the Phase 3 fixtures):
1. `formatted_pdf_path`: the title block yields three paragraphs in order with styles `normal` (eyebrow), `heading1` (title), `normal` (subtitle); eyebrow `color == "#00897c"` and `font_family == "monospace"`; subtitle `color == "#56635f"`; title `color is None`.
2. The two callout paragraphs share the same `box` index; `boxes[box] == PdfBox("#f3f6f5", "#9a5b2d")`; the paragraph before the callout has `box is None`.
3. Exactly two tables (page 1 with `header=True`, page 2 with `header=False`); for each, the referenced indices are contiguous, row-major and have style `"cell"`; `"L1222-3"` is a cell with `font_family == "monospace"`; the empty cell is `None`; no non-cell paragraph contains `"L1222-3"` or `"ARTICLE"`; header cells' box has background `"#f3f6f5"`.
4. `"2 Second section"` is a heading paragraph that follows the last cell of the first table.
5. `ruled_table_pdf_path`: one 3×3 table; the body paragraph above it comes first.
6. Invariants property check over every fixture (old and new): no `"\n"`, no empty text, `len(paragraphs_format) == len(paragraphs_text)`, table invariants, box indices valid.
7. Determinism: two extractions of `formatted_pdf_path` (path and bytes) are equal.
8. `extract_pdf_paragraphs(formatted_pdf_path, detect_layout=False)` → no tables, every `box is None`, and the table texts are present as non-cell paragraphs.
9. All existing `tests/test_pdf/test_extractor.py` tests pass **unchanged**.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 5 — Builder: formats, boxes, tables

**Goal.** Render paragraph formats, grouped background boxes and tables in the reflowed PDF.

**Files touched.** `src/core/pdf/builder.py` (modify), `tests/test_pdf/test_builder_formatting.py` (create).

**Concrete deliverables.** `build_pdf` signature unchanged. Normative behaviour:

1. **Validation** (raise `ValueError`, before any rendering): `paragraphs_format` non-empty with a length different from `paragraphs_text`; a table index outside `range(len(paragraphs_text))`; a table whose non-`None` indices are not the contiguous row-major range starting at `first_index`; a `box` index outside `boxes`.
2. **CSS.** `_PDF_CSS` becomes a template whose `body` rule uses `font-family: {family}` with `family = content.default_font_family` if it is in `FONT_FAMILIES`, else `"serif"`. Every other existing rule is kept byte-identical. Append exactly:
   ```css
   table.grid { width: 100%; border-collapse: collapse; margin: 6pt 0; }
   table.grid td, table.grid th { padding: 3pt 4pt; vertical-align: top; text-align: left; border-bottom: 0.5pt solid #cccccc; }
   table.grid th { font-weight: bold; }
   table.grid p { margin: 0; text-align: left; }
   div.box { padding: 4pt 6pt; margin: 6pt 0; }
   ```
3. **Element style.** `_text_element(text, style, rtl, source, fmt, default_family)`; the inline `style` attribute is built in this fixed order, parts joined by `"; "`, omitted entirely when empty:
   - `color:{fmt.color}` when `fmt.color` and not `source` (bilingual source text keeps the `.source` grey);
   - `font-family:{fmt.font_family}` when `fmt.font_family` is set and differs from the inherited family (`"sans-serif"` for `h1`–`h3`, `default_family` otherwise);
   - `font-weight:bold` when `fmt.bold` and the tag is not `h1`–`h3`;
   - `font-style:italic` when `fmt.italic`.
   Cell paragraphs use tag `p` with no class (inside `td`/`th`).
4. **Boxes.** Walking paragraphs in order (outside tables), consecutive paragraphs with the same non-`None` `box` are wrapped in one `<div class="box" style="background-color:{bg}">` (with `; border-left:2pt solid {border_left}` appended when set). The div closes before a paragraph with another or no box, before a table, and at the end. Images anchored to a boxed paragraph are emitted after it inside the open div. Bilingual source and translated elements of a boxed paragraph are both inside the div.
5. **Tables.** At index `i == t.first_index`, close any open box and emit `<table class="grid">`; per row `<tr>`; per cell `<th>` when `t.header and r == 0`, else `<td>`. Every cell gets `style="width:{w:.1f}pt"` with `w = max(10.0, t.column_widths[c] * cw - 8.0)` (`cw` = content width; absolute widths are required, percent widths are ignored by MuPDF); header cells whose format has a box append `; background-color:{boxes[box].background}`. Cell content: nothing for a `None` cell; otherwise, in bilingual mode with non-empty source, the source `<p class="source">`; then the translated `<p>` when non-blank. `dir="rtl"` rules are the same as for paragraphs (on the `<p>`). After `</table>`, emit the images anchored to any index of the table range, by increasing index. Rendering continues after the table's last index.
6. Everything else is unchanged: image scaling, RTL, bilingual order for non-table paragraphs, layout guard, metadata, error wrapping into `PdfBuildError`.
7. **Backward compatibility:** a `PdfPlainContent` with no `paragraphs_format`, `boxes` or `tables` renders exactly as before, except the `body` font-family now comes from `default_font_family` (default `"serif"`, the current value).

**Dependency.** Parallelizable (after Phase 1; runs alongside Phases 2, 3 and 4 — disjoint files).

**Risk surface.** Extractor ↔ builder seam (reads the Phase 1 model).

**Contract.** Raises only `ValueError` (validation, length mismatch) or `PdfBuildError`. The output text, in reading order, contains every non-empty translated paragraph and cell (cells in row-major order).

**Validation criteria** (`tests/test_pdf/test_builder_formatting.py`, content built by hand, output checked with `pymupdf.open`):
1. A paragraph with `ParagraphFormat(color="#00897c", font_family="monospace")` → its span colour is `0x00897c` and its font name contains `"Mono"`.
2. `default_font_family="sans-serif"` → a plain paragraph's font name contains `"Sans"`; default (`"serif"`) → it does not.
3. `bold=True` on a normal paragraph → its font name contains `"Bold"`.
4. Two paragraphs sharing `box=0` with `PdfBox("#f3f6f5", "#9a5b2d")` → the page drawings contain exactly one fill of colour ≈ `(0.953, 0.965, 0.961)` enclosing both paragraphs' text, and one fill ≈ `(0.604, 0.357, 0.176)` at its left edge.
5. A 3-row × 3-column table (header row with a box) → 9 cell texts present; the header row's texts have the same `y` (±1); three distinct column `x0` values, matching `margin + cumulative width` within ±8 pt for `column_widths=[0.11, 0.53, 0.36]`; a fill of the header colour exists; a `None` cell renders nothing.
6. Bilingual table → in each cell the source text precedes the translation.
7. `target_language="Hebrew"` → the Hebrew text of a cell is right-aligned inside its cell (`x0` greater than the cell's left edge + 30 % of its width).
8. Each validation error case raises `ValueError`.
9. All existing `tests/test_pdf/test_builder.py` tests pass **unchanged**.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 6 — Translator log, round-trip test, docs

**Goal.** Surface the table count, prove the extractor + builder round trip on the synthetic note, and document the new behaviour.

**Files touched.** `src/core/pdf/translator.py` (modify: one log message), `tests/test_pdf/test_formatting_roundtrip.py` (create), `README.md`, `docs/CLI.md`, `docs/BACKLOG.md` (modify).

**Concrete deliverables.**
1. `translator.py`: the `pdf_extracted` message becomes `f"📄 PDF: {content.page_count} pages, {paragraph_count} paragraphs, {len(content.tables)} tables, {n_images} images"` (same key). Nothing else changes.
2. `tests/test_pdf/test_formatting_roundtrip.py`: `extract_pdf_paragraphs(formatted_pdf_path)` then `build_pdf([f"T:{p}" for p in content.paragraphs_text], content, out, "French")`; assert on the output: a span with colour `0x00897c` and a `"Mono"` font (eyebrow); a heading span ≈ 20 pt; a box fill ≈ `#f3f6f5` and a bar fill ≈ `#9a5b2d`; `"T:L1222-3"` and `"T:ARTICLE"` present and on the same `y` (±1) as the other cells of their row; page count ≥ 1. Plus one test through `translate_pdf_file` with `translate_paragraphs_plain` patched (mirror `tests/test_pdf/test_translator.py`), asserting `success` and that the `pdf_extracted` log message contains `"2 tables"`.
3. Docs (plain prose, no em dashes, English):
   - `README.md` "PDF support." bullet: ruled tables are rebuilt as tables, and paragraph colours, background boxes and the font family (serif, sans-serif, monospace) are kept; inline bold or colour inside a sentence is not; multi-column layouts remain best effort.
   - `docs/CLI.md` PDF limitations line: same facts, one sentence.
   - `docs/BACKLOG.md` item 5.1 **Shipped.** paragraph: one sentence on track A (tables, colours, boxes, font family), and a new open item "PDF layout-preserving mode (track B)" in the backlog's existing style.

**Dependency.** Sequential (after Phases 4 and 5).

**Risk surface.** NONE (log text only; the checkpoint contract is untouched).

**Contract.** `translate_pdf_file` behaviour and return dicts unchanged apart from the log text.

**Validation criteria.**
- The tests above pass; existing `tests/test_pdf/test_translator.py` passes unchanged (it asserts log keys, not texts — verify).
- Manual check (record in the PR): translate a real text PDF with a ruled table (e.g. the user's own document, **not committed**) with the CLI `./venv/Scripts/python.exe translate.py -i <file.pdf> -tl English` and open the output: tables, the callout box, colours and families are visible.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green, then `./venv/Scripts/python.exe -m pytest -q` with no failure outside the 5 known characterization goldens.

**Ambiguity flag.** NONE.

---

## Risks and open questions

1. **Table heuristics on unseen generators.** The rule-grid detector is tuned on Chrome/Skia output (per-column `border-bottom` rules) and PyMuPDF's own detector covers fully ruled tables. LaTeX `booktabs` (3 full-width rules, no columns) and borderless tables are not detected and fall back to today's paragraph flattening. All thresholds are named module constants for later tuning.
2. **Translation of short cells.** Cells such as `"ARTICLE"` or article codes become their own units inside multi-unit chunks; the pipeline already aligns per paragraph, so the risk is stylistic (a code being "translated"), not structural. Accepted.
3. **Unit count change vs. pre-track-A checkpoints.** `resume_plain_segments` restarts a job whose stored `paragraph_count` differs. PDF support is unreleased, so no real checkpoint is affected. If track A ever ships separately from the base PDF support, this remains safe (restart, never misalignment) unless two extractions differ in content with the same count, which this blueprint's determinism tests make unlikely.
4. **Performance.** `find_tables()` and `get_drawings()` run on every page (roughly tens of milliseconds per page). Negligible next to LLM time for books; auxiliary features skip them (`detect_layout=False`).
5. **MuPDF CSS support.** Verified for every property used here on PyMuPDF 1.28.2. Percent column widths are ignored, hence absolute `pt` widths. A future PyMuPDF upgrade must re-run `test_builder_formatting.py`.
6. **Type3 fonts without a `FontDescriptor`** (older generators): family and bold stay unknown; the document default stays `"serif"`.
