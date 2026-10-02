# BLUEPRINT — PDF as input and output format

## Summary

Add PDF as a supported translation format: a user drops a PDF (web UI or CLI), the text is
extracted into paragraphs, translated through the existing **Plain Text Mode pipeline**
(`translate_paragraphs_plain`, with checkpoint/resume), and a **new, reflowed PDF** is
generated from the translated paragraphs (headings, paragraphs, list items, images placed
after their paragraph, original page size). This is backlog item 5.1 (issues #140, #234,
discussion #191), with the scope already agreed with the reporters: text PDFs, no layout
fidelity.

Decisions taken with the user (2026-10-01):

| Decision | Choice |
|---|---|
| Output rendering | **Reflowed PDF** built from translated paragraphs. Original layout is NOT preserved. |
| Format matrix | **PDF → PDF only** (the output format always equals the input format, as for EPUB/DOCX). |
| Feature surface | **Everything that reads source text**: translation, cost estimate, upload language detection, sample, glossary NER, style extraction, auto-prep. |

Planner decisions (not asked, stated here so no sub-agent has to infer them):

- **Library: PyMuPDF (`pymupdf`)**, used for both extraction and generation. Licence is AGPL-3.0,
  which matches the project licence (`LICENSE` = GNU AGPL v3). No other new dependency.
  Verified on PyMuPDF 1.28.2 (probe run during planning): `Story` + `DocumentWriter` render
  HTML to PDF; built-in fallback fonts cover Hebrew (Noto Serif Hebrew), Arabic (Noto Naskh
  Arabic, shaped), and CJK (Droid Sans Fallback); `dir="rtl"` on a `<p>` or `<div>` right-aligns
  (CSS `direction` does NOT); `<img>` with only a width keeps its aspect ratio; an image taller
  than the page is clipped onto one page (no infinite loop); `get_text("dict")` returns
  image blocks (`type == 1`) with raw bytes in `block["image"]` and the format in `block["ext"]`.
- **Scanned PDFs (no text layer) are rejected** with a clear error at translation time and a
  non-blocking warning at upload time. No OCR.
- **Password-protected PDFs are rejected** at upload.
- **Every PDF goes through the plain-text pipeline.** The `plain_text_mode` toggle is irrelevant
  for PDF (there is no markup to preserve). The PDF is never routed through `[idN]` placeholders.
- **Refine is not supported for PDF** in this blueprint: `refine_file` keeps raising
  `UnsupportedFormatError` for `.pdf`; the web "refine after" chain and the CLI `--refine` /
  `--refine-only` flags are skipped or rejected explicitly for PDF (Phase 4). TTS is untouched.
- **Multi-column layouts, tables, footnotes, vertical CJK text** are best-effort only. Blocks are
  read in PyMuPDF's native content-stream order (`sort=False`). This is a documented limitation,
  not a bug to fix in this blueprint.

---

## Project grounding

### Stack

Python 3.11 (Flask + Flask-SocketIO web app, argparse CLI), vanilla ES-module JS frontend
with a custom i18n layer, SQLite checkpoint DB, pytest. Entry points: `translate.py` (CLI),
`translation_api.py` (web server), `launcher.py` (packaged app). Packaged with PyInstaller
(`build/windows/TranslateBook.spec`, `build/macos/TranslateBook-macOS.spec`) and Docker
(`deployment/Dockerfile` installs `requirements.txt`).

### Conventions that constrain this change (from `CLAUDE.md`)

1. **English only** for everything committed: code, identifiers, comments, docstrings, docs,
   commit messages, user-facing strings in committed files. (Some legacy modules have French
   docstrings; do not copy that.)
2. **No secrets** in any file. Not relevant to this feature beyond the usual pre-commit check.
3. **Frontend i18n**: every user-facing string must use `data-i18n*` markers or `t('ns:key')`
   inside a render that re-runs on `localeChanged`. Any locale key added or changed must be
   changed in **all 7 locales** (`en`, `fr`, `es`, `de`, `zh-CN`, `ja`, `ko`) in the same commit;
   `{{placeholder}}` tokens and inline HTML tags must match the English source.
4. **No ad-hoc scripts at the repo root.** Tests go under `tests/` (pytest).
5. Mirror the surrounding code: DOCX is the reference implementation for a binary
   "document in → document out" format. New code follows its shape (module layout,
   `log_callback(key, message)` calls with emoji-prefixed messages, result dicts).

### Verification gate (quote verbatim to every sub-agent)

Bare `python` on this machine resolves to the wrong interpreter. Always use the project venv:

```
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q
./venv/Scripts/python.exe -m pytest -q
```

Known baseline: `tests/unit` was 1761 passed / 1 skipped at v1.5.10. The full run has **five
pre-existing failures** in `tests/characterization/test_progress_baseline.py`
(`test_translate_progress_baseline[docx|epub]`, `test_refine_progress_baseline[docx]`,
`test_translate_with_inline_refine_baseline[docx|epub]`). They are stale goldens on `main`,
not regressions: a phase is green when it adds no failure outside that list. No CI job runs
pytest, so the local gate is the only gate.

### Sacred / high-risk surfaces involved

| Surface | Why it is sensitive | Phases |
|---|---|---|
| **Upload security** (`src/utils/security.py`, `src/api/blueprints/security_routes.py`) | Security boundary for user-supplied files. PDF is a complex binary format parsed by a native library. | 5 |
| **Checkpoint / resume** (`checkpoint_manager.save/load_xhtml_partial_state`, `src/core/common/plain_text_checkpoint.py`) | Persistence format; a wrong `file_href` or a stale state corrupts a resume. PDF reuses the DOCX plain-text contract unchanged, with no schema change. | 4 |
| **Format routing** (`src/core/adapters/translate_file.py`, `src/utils/file_detector.py`) | Shared by web and CLI; a regression breaks every format. | 1, 4 |
| **Frontend i18n** (`src/web/static/locales/*`, `translation_interface.html`) | Enforced by `tests/test_frontend_i18n.py`; key parity across 7 locales. | 7 |
| **Packaging** (PyInstaller specs, `requirements.txt`) | A native wheel (PyMuPDF) must ship in the Windows/macOS executables and the Docker image. | 1 |

Prompts (`src/prompts/`) are **not** touched: the plain-text pipeline's prompts are reused as is.

---

## Architecture / integration notes

### Data flow

```
input.pdf
  └─ extract_pdf_paragraphs(path)            [NEW src/core/pdf/extractor.py]
        → PdfPlainContent(paragraphs_text, paragraphs_style, images_by_paragraph, page_size, title, author)
  └─ translate_paragraphs_plain(paragraphs)  [REUSED src/core/common/plain_text_pipeline.py]
        ↔ checkpoint hook / resume             [REUSED src/core/common/plain_text_checkpoint.py]
  └─ build_pdf(translated, content, ...)      [NEW src/core/pdf/builder.py]
        → output.pdf (PyMuPDF Story → DocumentWriter)
```

`translate_pdf_file` (NEW `src/core/pdf/translator.py`) does the same job as
`DocxTranslationAdapter._translate_plain_text`, without the `GenericTranslationOrchestrator`
layer: PDF has no placeholder path, so the orchestrator would be dead weight. It is routed
from `translate_file()` just like `translate_docx_file`.

### Integration points (exact contracts)

| File | Symbol | Contract used / changed |
|---|---|---|
| `src/core/common/plain_text_pipeline.py` | `async def translate_paragraphs_plain(paragraphs: List[str], source_language: str, target_language: str, model_name: str, llm_client: Any, max_tokens_per_chunk: int, log_callback=None, stats_callback=None, context_manager=None, check_interruption_callback=None, prompt_options=None, parallel_workers: int = 1, *, resume_segments=None, resume_translated=None, resume_statuses=None, resume_stats=None, checkpoint_hook=None, checkpoint_every: int = 5, count_failures_as_fallback: bool = False) -> Tuple[List[str], TranslationMetrics, bool]` | Reused unchanged. Returns `(translated_paragraphs, stats, was_interrupted)`, with `translated_paragraphs` parallel to the input. Paragraphs are joined internally with `PARAGRAPH_SEPARATOR = "\n\n"`. **Invariant the extractor must honour: no paragraph text may contain `"\n"`.** |
| `src/core/common/plain_text_checkpoint.py` | `resume_plain_segments(resume_state, paragraph_count, log_callback=None) -> (segments \| None, translated_prefix \| None)`; `build_plain_checkpoint_hook(*, checkpoint_manager, translation_id, file_href, source_language, target_language, model_name, max_tokens_per_chunk, max_retries, paragraph_count, prompt_options=None, bilingual=False, original_body_html="") -> Optional[Callable]`; `delete_plain_checkpoint(checkpoint_manager, translation_id, file_href, log_callback=None) -> None` | Reused unchanged, called exactly as `src/core/docx/docx_translation_adapter.py::_translate_plain_text` calls them (`original_body_html` left at its default `""`). |
| `src/persistence/checkpoint_manager.py` | `load_xhtml_partial_state(translation_id, file_href)` | Reused. `file_href = os.path.basename(input_filepath)`, the DOCX convention. |
| `src/core/llm` | `create_llm_provider(provider_type, endpoint, model, gemini_api_key, openai_api_key, openrouter_api_key, mistral_api_key, deepseek_api_key, poe_api_key)` | Called in `translate_file()` with **the same arguments as the DOCX branch** (copy it). |
| `src/core/epub/rtl_support.py` | `is_rtl_language(language: str) -> bool` | Used by the builder to set `dir="rtl"`. |
| `src/core/epub/translation_metrics.py` | `TranslationMetrics()` / `.to_dict()` | Stats object returned by the pipeline. |
| `src/config.py` | `ATTRIBUTION_ENABLED: bool`, `GENERATOR_NAME: str` | Builder stamps PDF metadata `producer`/`creator` when attribution is on (mirror of DOCX `core_properties.last_modified_by`). Read as `import src.config as cfg; cfg.ATTRIBUTION_ENABLED` at call time, like `build_minimal_docx`. |
| `src/core/adapters/translate_file.py` | `translate_file(...) -> bool`; `_ensure_checkpoint_job(...)`; `get_file_type_from_path(filepath) -> str` | Gains a `'pdf'` branch (Phase 4). |
| `src/utils/file_detector.py` | `FileType = Literal[...]`, `PROCESSOR_EXTENSIONS`, `detect_file_type(path) -> FileType`, `_detect_binary_format(path)` | Gains `'pdf'` (Phase 1). |
| `src/utils/security.py` | `SecureFileHandler.ALLOWED_EXTENSIONS`, `ALLOWED_MIME_TYPES`, the `extension_validators` dict and content-detection fallback in the validate method, `_validate_docx_file(file_path) -> FileValidationResult` (pattern) | Gains `_validate_pdf_file` (Phase 5). `FileValidationResult(is_valid, file_path, error_message, warnings)`. |
| `src/api/blueprints/security_routes.py` | upload route, extension → `file_type` chain (`.epub`/`.srt`/`.docx`/else `txt`) | Gains `.pdf → "pdf"` (Phase 5). The web client takes `fileType` from this response (`file-upload.js`: `fileType: uploadResult.file_type`). |
| `src/utils/language_detector.py` | `LanguageDetector.detect_language_from_file(file_data: bytes, filename: str, confidence_threshold=0.7) -> (Optional[str], float)` | Gains a `.pdf` branch (Phase 5). |
| `src/utils/document_sampler.py` | `TEXT_EXTS`, `RICH_EXTS`, `SUPPORTED_EXTS`, `extract_full_text(file_data: bytes, filename: str, hard_cap: int = FULL_TEXT_CAP) -> str \| None` | Gains `.pdf` in `RICH_EXTS` and `_extract_pdf_full_text` (Phase 6). This automatically covers auto-prep (`src/core/auto_prep.py::extract_source_text`), glossary NER (`glossary_routes.py` uses `document_sampler.SUPPORTED_EXTS`) and style extraction (`custom_instruction_routes.py` uses `document_sampler.SUPPORTED_EXTS`). |
| `src/api/blueprints/cost_routes.py` | suffix dispatch with `_extract_epub_text(Path) -> str` / `_extract_docx_text(Path) -> str` | Gains `.pdf` (Phase 6). |
| `src/api/blueprints/sample_routes.py` | `_extract_plain_text(file_path: str, file_type: str) -> str` | Gains `"pdf"` (Phase 6). `_validate_file` already relies on `detect_file_type`, so it picks up `'pdf'` from Phase 1. |
| `src/api/handlers.py` | `should_refine_after = (...)` block before the chained `refine_file(...)` call | Gains a PDF exclusion (Phase 4). |

---

## Phased breakdown

Dependency graph:

```
Phase 1 (foundations)
   ├── Phase 2 (extractor)  ─┐
   └── Phase 3 (builder)    ─┤   (2 and 3 in parallel)
                             ├── Phase 4 (translator + routing + CLI + handler + docs)
                             ├── Phase 5 (upload security + language detection)      (4, 5, 6, 7 in parallel)
                             ├── Phase 6 (auxiliary text consumers)
                             └── Phase 7 (frontend + i18n)
```

Phases 4–7 touch disjoint file sets and can run in parallel once 2 and 3 are merged.
Phase 7 depends only on Phase 1 for correctness, but its manual smoke test needs Phase 5.

---

### Phase 1 — Foundations: dependency, data model, format detection

**Goal.** Make `pymupdf` a dependency, define the shared PDF data model and exceptions, and teach the file detector that `.pdf` is a format.

**Files touched.**
- `requirements.txt` (modify)
- `build/windows/TranslateBook.spec` (modify)
- `build/macos/TranslateBook-macOS.spec` (modify)
- `src/core/pdf/__init__.py` (create)
- `src/core/pdf/content.py` (create)
- `src/utils/file_detector.py` (modify)
- `tests/test_pdf/__init__.py` (create, empty)
- `tests/test_pdf/test_file_detection.py` (create)

**Concrete deliverables.**

1. `requirements.txt`: append after the DOCX block:
   ```
   # PDF support (AGPL-3.0, same licence as this project)
   pymupdf>=1.26.0
   ```
2. Both `.spec` files: add `'pymupdf'` to `hiddenimports`, right after `'PIL'`.
3. `src/core/pdf/content.py`:
   ```python
   PARAGRAPH_STYLES = ("heading1", "heading2", "heading3", "list", "normal")

   @dataclass
   class PdfImage:
       data: bytes          # encoded image bytes (png or jpeg)
       ext: str             # "png" or "jpeg" only
       width_pt: float      # displayed width on the source page, in points
       height_pt: float     # displayed height on the source page, in points

   @dataclass
   class PdfPlainContent:
       paragraphs_text: List[str] = field(default_factory=list)
       paragraphs_style: List[str] = field(default_factory=list)   # parallel to paragraphs_text, values in PARAGRAPH_STYLES
       images_by_paragraph: Dict[int, List[PdfImage]] = field(default_factory=dict)  # key -1 = before the first paragraph
       page_size: Optional[Tuple[float, float]] = None   # (width_pt, height_pt) of the first source page
       title: str = ""
       author: str = ""
       page_count: int = 0

   class PdfError(Exception): """Base class for PDF processing errors."""
   class PdfOpenError(PdfError): """File is not a readable PDF."""
   class PdfEncryptedError(PdfError): """PDF requires a password."""
   class PdfNoTextLayerError(PdfError): """PDF has no extractable text (scanned document)."""
   class PdfBuildError(PdfError): """Output PDF could not be generated."""
   ```
   English docstrings. No PyMuPDF import in this module (pure data).
4. `src/core/pdf/__init__.py`: module docstring "PDF translation module." and re-export of the
   `content.py` names only (Phase 4 adds `translate_pdf_file`, Phase 2 adds `extract_pdf_text`).
   **Must not import pymupdf at package import time.**
5. `src/utils/file_detector.py`:
   - `FileType = Literal["txt", "epub", "srt", "docx", "pdf"]`
   - `PROCESSOR_EXTENSIONS['.pdf'] = 'pdf'`
   - `_detect_binary_format`: read the first **1024** bytes instead of 64; if `b'%PDF-' in header` → return `'pdf'`. Keep the ZIP `PK` check first, unchanged. (The PDF spec allows junk before the `%PDF-` header within the first 1024 bytes.)
   - Docstrings and the `ValueError` message list `.pdf`: `"Supported types: .txt, .epub, .srt, .docx, .pdf, or plain text files with any extension."`
   - `'.pdf'` must NOT be added to `KNOWN_TEXT_EXTENSIONS`.

**Dependency.** Sequential (first).

**Risk surface.** Packaging (requirements, PyInstaller specs); Format routing (`file_detector.py`).

**Contract.**
- `detect_file_type("x.pdf") == "pdf"` without reading the file.
- `detect_file_type("x.bin")` on a file starting with `%PDF-1.7` → `"pdf"`; with 100 junk bytes then `%PDF-` → `"pdf"`.
- `detect_file_type_by_content` on an EPUB/DOCX/TXT/SRT file returns exactly what it returned before (no regression from the 64 → 1024 byte read).
- `import src.core.pdf` succeeds even when `pymupdf` is not installed.

**Validation criteria.**
- `tests/test_pdf/test_file_detection.py`: the four assertions above (`tmp_path` files, bytes written with `write_bytes`), plus one test that a `PK\x03\x04...` file is not detected as PDF.
- `./venv/Scripts/python.exe -m pip install -r requirements.txt` then `./venv/Scripts/python.exe -c "import pymupdf; print(pymupdf.__version__)"` prints a version ≥ 1.26.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 2 — PDF extractor (PDF → paragraphs)

**Goal.** Turn a text PDF into an ordered list of clean paragraphs with a coarse style and anchored images, and expose a never-raising plain-text helper for the auxiliary features.

**Files touched.**
- `src/core/pdf/extractor.py` (create)
- `src/core/pdf/__init__.py` (modify: export `extract_pdf_paragraphs`, `extract_pdf_text`, via lazy import inside thin wrapper functions so the package still imports without pymupdf)
- `tests/test_pdf/conftest.py` (create)
- `tests/test_pdf/test_extractor.py` (create)

**Concrete deliverables.**

Public API in `extractor.py`:

```python
def extract_pdf_paragraphs(source: Union[str, bytes], *, include_images: bool = True) -> PdfPlainContent
def extract_pdf_text(source: Union[str, bytes], hard_cap: Optional[int] = None) -> str
```

Module constants (exact values, module-level, so tests and later tuning reference them):

```python
HEADER_FOOTER_BAND = 0.07        # fraction of page height at top and bottom
REPEAT_MIN_PAGES = 3             # repetition rule needs at least this many pages
REPEAT_RATIO = 0.5               # signature present on >= 50% of pages
H1_RATIO, H2_RATIO, H3_RATIO = 1.6, 1.3, 1.15
HEADING_MAX_CHARS = 200
BOLD_HEADING_MAX_CHARS = 80
BOLD_FLAG = 16                   # PyMuPDF span flag bit for bold
MIN_IMAGE_SIDE_PT = 24.0
MIN_CHARS_PER_PAGE = 20          # scanned-document threshold (average non-whitespace chars per page)
TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|of)\s*\d{1,4})?\s*$", re.I)
LIST_MARKER_RE = re.compile(r"^\s*(?:[•◦▪▫‣⁃●○■□*·]|-(?=\s)|\(?\d{1,3}[.)]|\(?[A-Za-z][.)])\s+")
TERMINAL_PUNCT_RE = re.compile(r"[.!?…:;\"»”’)\]。！？」』）]\s*$")
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
```

(`—` and `–` are deliberately NOT list markers: they open dialogue lines in French, Spanish and Russian prose.)

`extract_pdf_paragraphs` algorithm (normative, in this order):

1. **Open.** `pymupdf.open(source)` for a `str` path, `pymupdf.open(stream=source, filetype="pdf")` for `bytes`. Any exception → `PdfOpenError(str(exc))`. If `doc.needs_pass` → `PdfEncryptedError("This PDF is password-protected. Remove the password and try again.")`. If `doc.page_count == 0` → `PdfOpenError("The PDF has no pages.")`.
2. **Collect.** For every page: `page.get_text("dict", flags=TEXT_FLAGS, sort=False)["blocks"]`, keeping per block its page index, `page.rect.height`, the block dict. Text blocks are `type == 0`; image blocks are `type == 1` (only collected when `include_images`).
3. **Block text.** For a text block, the text of a line is the concatenation of its spans' `text`. Lines are joined with `join_lines(prev, nxt)`:
   - (a) if `prev` ends with U+00AD → `prev[:-1] + nxt.lstrip()`;
   - (b) elif `prev` ends with `<letter>-` (ASCII hyphen preceded by `str.isalpha()`) and `nxt.lstrip()[:1].islower()` → `prev[:-1] + nxt.lstrip()`;
   - (c) elif the last char of `prev.rstrip()` and the first char of `nxt.lstrip()` are both CJK, meaning in `　-〿`, `぀-ヿ`, `㐀-䶿`, `一-鿿`, `豈-﫿`, `＀-￯` (Hangul is excluded: Korean wraps at spaces) → `prev.rstrip() + nxt.lstrip()`;
   - (d) else `prev.rstrip() + " " + nxt.lstrip()`.

   After joining: replace every `LIGATURES` key, remove any remaining U+00AD, then `re.sub(r"\s+", " ", text).strip()`. **This guarantees no `"\n"` in any paragraph.**
4. **Header/footer removal.** A text block is *in band* if `bbox.y1 <= HEADER_FOOTER_BAND * page_h` or `bbox.y0 >= (1 - HEADER_FOOTER_BAND) * page_h`. Its *signature* is `re.sub(r"\d+", "#", text.lower())` with whitespace collapsed. Count, per signature, the number of **distinct pages** where it appears in band. Drop an in-band block when `PAGE_NUMBER_RE.fullmatch(text)` matches, or when `doc.page_count >= REPEAT_MIN_PAGES` and its signature's page count `>= max(REPEAT_MIN_PAGES, ceil(REPEAT_RATIO * doc.page_count))`.
5. **Body size.** Over all kept text blocks, accumulate `len(span.text.strip())` per `round(span.size * 2) / 2`. `body_size` is the size with the largest total (ties → the smaller size). If the document has fewer than `MIN_CHARS_PER_PAGE * doc.page_count` non-whitespace characters in kept blocks → `PdfNoTextLayerError("This PDF has no extractable text layer (scanned document?). OCR is not supported: convert it with an OCR tool first.")`.
6. **Block style.** `block_size` = the char-weighted dominant rounded span size of the block (same rule as step 5, restricted to the block). `ratio = block_size / body_size`. Let `text` be the block text and `all_bold` = every non-blank span has `flags & BOLD_FLAG`.
   - `len(text) <= HEADING_MAX_CHARS` and `ratio >= H1_RATIO` → `heading1`; `>= H2_RATIO` → `heading2`; `>= H3_RATIO` → `heading3`;
   - elif `all_bold` and `len(text) <= BOLD_HEADING_MAX_CHARS` and the block has ≤ 2 lines and `block_size >= body_size - 0.5` and `text` does not end with one of `.,;:` → `heading3`;
   - else `normal`.
7. **List splitting** (only for `normal` blocks). Walk the block's lines. A line whose text matches `LIST_MARKER_RE` (and is not the first line) starts a new paragraph. Each resulting paragraph whose text matches `LIST_MARKER_RE` gets style `list`; the others stay `normal`. The marker is **kept verbatim** in the text.
8. **Cross-block / cross-page merge.** Before appending a new `normal` paragraph `P`, merge it into the last emitted paragraph `Q` (same `join_lines` rules on their texts) when **all** hold: `Q.style == "normal"`, `TERMINAL_PUNCT_RE.search(Q.text)` is None, and `P.text[:1].islower()`, or when `Q.text` ends with `<letter>-` and `P.text[:1].islower()`. This is the page-boundary reassembly. Headings and list items are never merged.
9. **Images** (`include_images=True` only). For an image block: skip if `bbox.width < MIN_IMAGE_SIDE_PT` or `bbox.height < MIN_IMAGE_SIDE_PT`; skip if `sha1(block["image"])` appears on `>= max(REPEAT_MIN_PAGES, ceil(REPEAT_RATIO * doc.page_count))` distinct pages (logos, backgrounds; first pass counts hashes). `ext` normalisation: `"png"` stays, `"jpeg"`/`"jpg"` → `"jpeg"`, anything else → convert with `pymupdf.Pixmap(block["image"])` (if `pix.alpha` or `pix.n - pix.alpha > 3`, first `pix = pymupdf.Pixmap(pymupdf.csRGB, pix)`), then `pix.tobytes("png")` with `ext="png"`; a conversion exception drops the image silently. Anchor to key `len(paragraphs) - 1` at the moment the block is met in reading order (`-1` if no paragraph yet). `width_pt = bbox.width`, `height_pt = bbox.height`.
10. **Metadata.** `page_size = (doc[0].rect.width, doc[0].rect.height)`, `title = (doc.metadata or {}).get("title") or ""`, `author = ... "author"`, `page_count = doc.page_count`. Close the document in a `finally`.
11. Empty paragraphs are never emitted. `len(paragraphs_text) == len(paragraphs_style)` always.

`extract_pdf_text(source, hard_cap=None)`: calls `extract_pdf_paragraphs(source, include_images=False)`, returns `"\n\n".join(paragraphs_text)`, truncated to `hard_cap` when `hard_cap` is a positive int. **Never raises**: any exception (including `ImportError` for pymupdf and every `PdfError`) → `""`.

`tests/test_pdf/conftest.py`: fixtures that **generate PDFs with PyMuPDF** (no binary fixtures committed), using `doc = pymupdf.open(); page = doc.new_page(width=595, height=842); page.insert_text((x, y), text, fontsize=..., fontname="helv"|"hebo")` and `doc.save(path)`:
- `simple_pdf_path`: one 20pt `hebo` title line, then two 11pt `helv` body paragraphs as separate blocks (start them ≥ 30pt apart vertically so PyMuPDF makes separate blocks).
- `multipage_pdf_path`: 4 pages; each page has a top line `"My Book"` at y=30, a bottom line with the page number (`"1"`..`"4"`) at y=820, and body text. Page 1's last body line ends with `"the conti-"`, page 2's first body line starts with `"nuation of the sentence."`.
- `list_pdf_path`: one block made of three lines `"• first item"`, `"• second item"`, `"1) third item"`.
- `image_pdf_path`: a body paragraph, then `page.insert_image(pymupdf.Rect(72, 200, 272, 300), stream=<200x100 PNG built with Pillow>)`, then another paragraph below it; plus a 10x10pt image (must be dropped).
- `scanned_pdf_path`: 2 pages that only contain an inserted image, no text.
- `encrypted_pdf_path`: `doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")`.

**Dependency.** Parallelizable (after Phase 1; independent of Phase 3).

**Risk surface.** NONE (new isolated module). It parses untrusted input, but every PyMuPDF call sits behind the exception contract above.

**Contract.**
- Input: a path or bytes of a PDF. Output: `PdfPlainContent` satisfying: parallel lists, style values in `PARAGRAPH_STYLES`, no `"\n"` in any paragraph, no empty paragraph, image keys in `[-1, len(paragraphs_text) - 1]`, every `PdfImage.ext in {"png", "jpeg"}`.
- Raises only `PdfOpenError`, `PdfEncryptedError`, `PdfNoTextLayerError` (and `ImportError` if pymupdf is missing).
- Deterministic: the same input gives the same output.
- `extract_pdf_text` never raises.

**Validation criteria** (`tests/test_pdf/test_extractor.py`, all must pass):
1. `simple_pdf_path` → 3 paragraphs; styles `["heading1", "normal", "normal"]` (20/11 = 1.82 ≥ 1.6).
2. `multipage_pdf_path` → no paragraph equals `"My Book"` or a bare page number; one paragraph contains `"the continuation of the sentence."` (hyphen joined, page boundary merged).
3. `list_pdf_path` → 3 paragraphs, all style `list`, texts start with `"• first item"`, `"• second item"`, `"1) third item"`.
4. `image_pdf_path` → exactly one image kept, anchored to index 0, `ext == "png"`, `width_pt ≈ 200` (±1).
5. `scanned_pdf_path` → `PdfNoTextLayerError`; `extract_pdf_text(...) == ""`.
6. `encrypted_pdf_path` → `PdfEncryptedError`; `extract_pdf_text(...) == ""`.
7. `b"not a pdf"` → `PdfOpenError`; `extract_pdf_text(b"not a pdf") == ""`.
8. Unit tests of `join_lines` for rules (a)–(d), including `("中文", "段落")` → `"中文段落"` and `("한국어", "문장")` → `"한국어 문장"`.
9. `extract_pdf_text(simple_pdf_path, hard_cap=10)` returns exactly 10 chars; bytes and path inputs give identical results.
10. Property check over every fixture: no `"\n"` in any paragraph.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE. The thresholds are fixed above. Tuning them on real-world PDFs is a follow-up, not part of this phase.

---

### Phase 3 — PDF builder (translated paragraphs → PDF)

**Goal.** Generate a clean reflowed PDF from translated paragraphs and the extracted `PdfPlainContent`.

**Files touched.**
- `src/core/pdf/builder.py` (create)
- `src/core/pdf/__init__.py` (modify: export `build_pdf` through a lazy wrapper; **coordinate with Phase 2**, see note)
- `tests/test_pdf/test_builder.py` (create)

> Parallelism note: Phases 2 and 3 both add one export to `src/core/pdf/__init__.py`. To keep
> them truly parallel, **Phase 3 does NOT edit `__init__.py`**: its tests import
> `from src.core.pdf.builder import build_pdf` directly, and Phase 4 adds the `build_pdf`
> re-export. Phase 2 is the only parallel phase that edits `__init__.py`.

**Concrete deliverables.**

```python
def build_pdf(
    translated_paragraphs: List[str],
    content: PdfPlainContent,
    output_path: str,
    target_language: str,
    source_language: str = "",
    bilingual: bool = False,
) -> None
```

Normative behaviour:

1. `len(translated_paragraphs) != len(content.paragraphs_text)` → `ValueError` naming both lengths.
2. **Page geometry.** `(w, h) = content.page_size or pymupdf.paper_size("a4")`. `margin = min(72.0, max(36.0, 0.1 * min(w, h)))`. `mediabox = pymupdf.Rect(0, 0, w, h)`; `where = mediabox + (margin, margin, -margin, -margin)`. `cw, ch = where.width, where.height`.
3. **HTML.** Build a string. Every text goes through `html.escape(text, quote=False)`. Tag per style: `heading1/2/3` → `<h1>/<h2>/<h3>`, `list` → `<p class="list">`, `normal` → `<p>`. Direction attribute: translated elements get `dir="rtl"` when `is_rtl_language(target_language)`, else no attribute; bilingual source elements get `dir="rtl"` when `is_rtl_language(source_language)`. For each index `i` in order:
   - if `bilingual` and the source text is non-empty: emit the source element with the same tag and an extra class `source` (for `<p class="list">`, use `class="list source"`);
   - if `translated_paragraphs[i].strip()` is non-empty: emit the translated element;
   - emit the images anchored to `i`.

   Images anchored to `-1` are emitted before index 0. Image element: `<p class="img"><img src="img_{key}_{k}.{ext}" style="width:{W:.1f}pt;height:{H:.1f}pt"/></p>` where `scale = min(1.0, cw / width_pt, 0.9 * ch / height_pt)`, `W = width_pt * scale`, `H = height_pt * scale` (`key` is `m1` for -1). Each image's bytes are added with `archive.add(img.data, f"img_{key}_{k}.{ext}")` on one `pymupdf.Archive()`.
4. **CSS** (passed as `user_css`, exact):
   ```css
   body { font-family: serif; font-size: 11pt; line-height: 1.4; }
   p { margin: 0 0 6pt 0; text-align: justify; }
   h1, h2, h3 { font-family: sans-serif; font-weight: bold; margin: 12pt 0 6pt 0; }
   h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
   p.list { margin-left: 18pt; text-align: left; }
   .source { color: #666666; }
   p.img { text-align: center; margin: 6pt 0; }
   ```
5. **Rendering.** `story = pymupdf.Story(html=html, user_css=css, archive=archive)`; write into an `io.BytesIO` via `pymupdf.DocumentWriter(buffer)` with the loop `while more: dev = writer.begin_page(mediabox); more, _ = story.place(where); story.draw(dev); writer.end_page()`. Guard: if the page count exceeds `10 * (len(translated_paragraphs) + total_images) + 10`, abort with `PdfBuildError("PDF layout did not converge")`. `writer.close()`.
6. **Metadata and save.** Reopen with `pymupdf.open("pdf", buffer.getvalue())`, then `set_metadata({"title": content.title, "author": content.author, "producer": GENERATOR_NAME if attribution else "", "creator": GENERATOR_NAME if attribution else ""})`, then `save(output_path, garbage=3, deflate=True)`. Read attribution at call time: `import src.config as cfg; cfg.ATTRIBUTION_ENABLED`.
7. Any PyMuPDF exception during steps 5–6 → re-raised as `PdfBuildError(str(exc))` (chained with `from`).
8. An all-empty translation (every paragraph blank, no images) still writes a valid 1-page PDF.

**Dependency.** Parallelizable (after Phase 1; independent of Phase 2).

**Risk surface.** NONE (new isolated module).

**Contract.** Input: translated paragraphs parallel to `content.paragraphs_text`. Output: a valid PDF at `output_path` whose text, in reading order, contains every non-empty translated paragraph (and, in bilingual mode, each source paragraph right before its translation), with images in anchor order. Raises only `ValueError` (length mismatch) or `PdfBuildError`.

**Validation criteria** (`tests/test_pdf/test_builder.py`; build `PdfPlainContent` by hand, verify with `pymupdf.open(output_path)`):
1. Three paragraphs (`heading1`, `normal`, `list`) → `"".join(page.get_text() for page in doc)` contains all three in order; the heading span size ≈ 20 (read via `get_text("dict")`).
2. `page_size=(420, 595)` → every output page has `rect.width == 420` and `rect.height == 595`.
3. 300 normal paragraphs of 400 chars → `page_count > 1`, and the last paragraph's text is present.
4. `target_language="Hebrew"` → the paragraph's block `x0 > w / 2` (right-aligned); `"French"` → `x0 < w / 2`.
5. `target_language="Chinese"` with CJK text → text extracts back identically (fallback font embedded).
6. Bilingual → the source text appears before the translated text for each pair.
7. Image anchored to 0 with `width_pt=2000` (wider than the page) → an image block exists and its bbox width ≤ `cw + 1`.
8. Length mismatch → `ValueError`.
9. With `monkeypatch.setattr(src.config, "ATTRIBUTION_ENABLED", True)` → `doc.metadata["producer"] == src.config.GENERATOR_NAME`; with `False` → `""`.
10. HTML injection: the paragraph `"<b>x</b> & y"` comes back as literal text.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 4 — Translation entry point, routing, CLI, refine guard, docs

**Goal.** Wire extractor + plain pipeline + builder into `translate_pdf_file`, route `.pdf` through `translate_file()` (web + CLI), guard the refine paths, and document the feature.

**Files touched.**
- `src/core/pdf/translator.py` (create)
- `src/core/pdf/__init__.py` (modify: export `translate_pdf_file` and `build_pdf` via lazy wrappers)
- `src/core/adapters/translate_file.py` (modify)
- `src/api/handlers.py` (modify: refine-after guard only)
- `translate.py` (modify)
- `tests/test_pdf/test_translator.py` (create)
- `tests/test_pdf/test_translate_file_routing.py` (create)
- `README.md` (modify)
- `docs/CLI.md` (modify)
- `docs/BACKLOG.md` (modify: item 5.1)
- `.github/workflows/build-windows.yml`, `.github/workflows/build-macos.yml` (modify: "Supported formats" release-note line only)

**Concrete deliverables.**

1. `src/core/pdf/translator.py`:
   ```python
   async def translate_pdf_file(
       input_filepath: str, output_filepath: str, source_language: str, target_language: str,
       model_name: str, llm_client: Any, max_tokens_per_chunk: int = 450,
       log_callback: Optional[Callable] = None, stats_callback: Optional[Callable] = None,
       prompt_options: Optional[Dict] = None, max_retries: int = 1, context_manager: Optional[Any] = None,
       check_interruption_callback: Optional[Callable] = None, checkpoint_manager: Optional[Any] = None,
       translation_id: Optional[str] = None, parallel_workers: int = 1, **kwargs,
   ) -> Dict[str, Any]
   ```
   Steps (mirror `DocxTranslationAdapter._translate_plain_text`, adapted):
   1. `file_href = os.path.basename(input_filepath)`; `bilingual = bool(prompt_options.get('bilingual')) if prompt_options else False`.
   2. `resume_state = checkpoint_manager.load_xhtml_partial_state(translation_id, file_href)` when both are set, else `None`.
   3. `content = extract_pdf_paragraphs(input_filepath)`. **`PdfError` subclasses propagate** (not caught): `translate_file` lets them reach the web handler's generic `except Exception`, which marks the job `error` with the message, and the CLI prints it. Before re-raising, call `log_callback("pdf_extraction_failed", f"❌ {exc}")` when a callback exists.
   4. `log_callback("pdf_extracted", f"📄 PDF: {content.page_count} pages, {len(content.paragraphs_text)} paragraphs, {n_images} images")`.
   5. `resume_segments, resume_translated = resume_plain_segments(resume_state, len(content.paragraphs_text), log_callback)`; `checkpoint_hook = build_plain_checkpoint_hook(...)`, with the same keyword arguments as DOCX (`paragraph_count=len(content.paragraphs_text)`, `bilingual=bilingual`).
   6. `translated, stats, was_interrupted = await translate_paragraphs_plain(paragraphs=content.paragraphs_text, ..., parallel_workers=parallel_workers, resume_segments=..., resume_translated=..., checkpoint_hook=...)`.
   7. Interrupted → `log_callback("pdf_interrupted", "⏸️ PDF translation interrupted - state saved")`, return `{'success': False, 'stats': stats.to_dict(), 'output_path': None, 'interrupted': True}` and write no output file.
   8. Else `build_pdf(translated, content, output_filepath, target_language, source_language, bilingual)`; `log_callback("pdf_rebuilt", f"📄 PDF rebuilt ({os.path.getsize(output_filepath)} bytes)")`; `delete_plain_checkpoint(checkpoint_manager, translation_id, file_href, log_callback)`; return `{'success': True, 'stats': stats.to_dict(), 'output_path': output_filepath}`.
2. `translate_file.py`:
   - Module docstring and `translate_file` docstring list PDF.
   - `if detected_type in ('epub', 'docx', 'pdf'):` for `_ensure_checkpoint_job`.
   - New branch `if detected_type == 'pdf':` placed right after the DOCX branch, a **copy of the DOCX branch** with `translate_pdf_file` (same `create_llm_provider(...)` arguments, `max_retries=1`, `context_manager=None`) returning `result.get('success', False)`.
   - Both `supported = ', '.join([...])` lists include `'pdf'`.
   - `get_file_type_from_path`: add `'.pdf': 'pdf'`; update its docstring.
   - `build_translated_output`: unchanged (add `# Note: pdf doesn't support checkpoint reconstruction yet` next to the docx note).
3. `src/api/handlers.py`: in `should_refine_after`, add the condition `and config.get('file_type') != 'pdf'`. Immediately after that assignment, add: if `config.get('refine_after')` and `config.get('file_type') == 'pdf'`, then `_log_message_callback("refine_after_unsupported_pdf", "ℹ️ Refinement is not available for PDF files yet; the translated PDF is kept as is.")`. Nothing else in the handler changes.
4. `translate.py`:
   - `description=` and the `-i/--input` help mention PDF: `"Translate a text, EPUB, SRT, DOCX or PDF file using an LLM."` / `"Path to the input file (text, EPUB, SRT, DOCX or PDF)."`
   - File-type label block: add `elif args.input.lower().endswith('.pdf'): file_type = "PDF"` (and `.docx` → `"DOCX"` while there, since it currently prints "TEXT").
   - PDF refine guard, placed right after `args = parser.parse_args()`-time validation (next to the other `parser.error` checks): `if args.input.lower().endswith('.pdf'):` then `if args.refine_only: parser.error("--refine-only does not support PDF input yet.")`, and `if args.refine:` set `logger.warning("⚠️ --refine is ignored for PDF input (not supported yet).")` and `args.refine = False`. **Place the `--refine` warning after `logger` is created.**
   - The output name logic already keeps the `.pdf` extension (`output_ext = ext`); no change.
5. Docs (same phase, since this phase changes the user-facing contract):
   - `README.md`: `**Formats:** EPUB, SRT, DOCX, PDF, TXT`. Add a short "PDF" paragraph in the features or limitations area: text PDFs only (no OCR), the output is a new reflowed PDF (original layout not kept), multi-column/table layouts are best effort, no refine for PDF yet. Plain prose, no em dashes.
   - `docs/CLI.md`: `-i, --input` row lists `.pdf`; add an example `# PDF (auto-generates "paper (French).pdf")` / `python translate.py -i paper.pdf -tl French`, and one line on the PDF limitations and the `--refine` behaviour.
   - `docs/BACKLOG.md` item 5.1: mark `[x]`, add a **Shipped.** paragraph summarising scope (PyMuPDF, reflowed output, plain-text pipeline, no OCR, no refine) in the backlog's existing style.
   - Workflows: the `- Supported formats: ...` line becomes `.txt, .epub, .srt, .docx, .pdf, .odt` (keep `.odt` as is, out of scope).

**Dependency.** Sequential (after Phases 2 and 3).

**Risk surface.** **Format routing** (`translate_file.py`); **Checkpoint / resume** (reuses the plain-text partial-state contract and `file_href` convention; no schema change); `handlers.py` refine chain.

**Contract.**
- `translate_file(input_filepath="x.pdf", ...)` dispatches to `translate_pdf_file` and returns `True` on success, `False` when interrupted.
- An interrupted PDF job resumed with the same `translation_id` restarts at the first unattempted segment (reuses `resume_plain_segments`) and finally deletes its partial state.
- A scanned/encrypted/corrupt PDF makes `translate_file` raise the corresponding `PdfError` subclass. No output file is written.
- TXT/SRT/EPUB/DOCX routing is byte-for-byte unchanged.
- Web: `refine_after=True` on a PDF job → translation completes with status `completed`, refine is skipped with the info log, no exception.

**Validation criteria.**
- `tests/test_pdf/test_translator.py` (uses the Phase 2 conftest fixtures; patch `src.core.pdf.translator.translate_paragraphs_plain` with an `AsyncMock` returning `([f"T:{p}" for p in paragraphs], TranslationMetrics(), False)`):
  1. Success → output PDF exists, its text contains `"T:"` + the first paragraph, and the result dict is `success=True`.
  2. Interrupted (`was_interrupted=True`) → `success=False`, `interrupted=True`, no output file.
  3. Scanned fixture → raises `PdfNoTextLayerError` and the `log_callback` received key `"pdf_extraction_failed"`.
  4. Bilingual (`prompt_options={'bilingual': True}`) → source and `T:` text both present.
  5. With a `Mock()` checkpoint manager whose `load_xhtml_partial_state` returns `None` and `translation_id="job1"`: after success, `checkpoint_manager.delete_xhtml_partial_state` was called with `("job1", "<fixture basename>.pdf")`; after an interruption it was NOT called.
- `tests/test_pdf/test_translate_file_routing.py`: patch `src.core.llm.create_llm_provider` and `src.core.pdf.translator.translate_pdf_file`, call `translate_file` on `simple_pdf_path`, assert the PDF function was awaited once and the return value is `True`; assert `get_file_type_from_path("a.PDF") == "pdf"`.
- Manual CLI smoke test (record the result in the PR): `./venv/Scripts/python.exe translate.py -i <a real text PDF> -tl French` with a configured provider produces `<name> (French).pdf`, which opens in a PDF viewer.
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green, then `./venv/Scripts/python.exe -m pytest -q` with no failure outside the 5 known characterization goldens.

**Ambiguity flag.** NONE.

---

### Phase 5 — Upload security and language detection

**Goal.** Accept PDF uploads safely in the web app, type them as `pdf`, and detect their language.

**Files touched.**
- `src/utils/security.py` (modify)
- `src/api/blueprints/security_routes.py` (modify)
- `src/utils/language_detector.py` (modify)
- `tests/unit/test_pdf_upload_security.py` (create)

**Concrete deliverables.**

1. `security.py`:
   - `ALLOWED_EXTENSIONS` gains `'.pdf'` (in the "Primary supported formats" line); `ALLOWED_MIME_TYPES` gains `'application/pdf'`.
   - `extension_validators['.pdf'] = self._validate_pdf_file`; in the unknown-extension content fallback, `elif detected_type == 'pdf': return self._validate_pdf_file(file_path)`.
   - `_validate_pdf_file(self, file_path: Path) -> FileValidationResult`, modelled on `_validate_docx_file`:
     1. read the first 1024 bytes; no `b'%PDF-'` → invalid, `"File is not a valid PDF (missing %PDF header)"`;
     2. `import pymupdf` inside the method; `ImportError` → invalid, `"PDF support is not installed (missing pymupdf)"`;
     3. open with `pymupdf.open(file_path)` in `try/finally doc.close()`; exception → invalid, `f"PDF validation failed: {exc}"`;
     4. `doc.needs_pass` → invalid, `"Password-protected PDFs are not supported. Remove the password and try again."`;
     5. `doc.page_count == 0` → invalid, `"The PDF has no pages."`; `doc.page_count > 5000` → invalid, `"PDF has too many pages (max 5000)."`;
     6. text-layer probe: sum `len(page.get_text().strip())` over the first `min(10, page_count)` pages; below `20 * pages_probed` → **valid with a warning** `"This PDF seems to contain no text layer (scanned document?). Translation will fail: OCR is not supported."`;
     7. otherwise valid. Return `FileValidationResult(is_valid=True, file_path=file_path, warnings=[...])` exactly like the DOCX validator does.
2. `security_routes.py`: in the extension chain add `elif original_filename.endswith('.pdf'): file_type = "pdf"` before the `else`.
3. `language_detector.py`: in `detect_language_from_file`, add before the plain-text `else`: `elif filename_lower.endswith('.pdf'): text = LanguageDetector._extract_text_from_pdf(file_data)`, with a new `@staticmethod _extract_text_from_pdf(file_data: bytes) -> str` returning `extract_pdf_text(file_data, hard_cap=20000)` (import from `src.core.pdf.extractor` inside the method). Empty text then flows into the existing "not enough text" path, returning `(None, 0.0)`.

**Dependency.** Parallelizable (after Phase 2; disjoint from Phases 4, 6, 7).

**Risk surface.** **Upload security** (security boundary).

**Contract.**
- A valid text PDF upload is accepted and the upload response carries `file_type == "pdf"`.
- Non-PDF bytes renamed `.pdf`, an encrypted PDF, and a 0-page or corrupt PDF are rejected with the messages above.
- A scanned PDF is accepted with the warning.
- Validation of every other extension is unchanged.
- `detect_language_from_file(pdf_bytes, "a.pdf")` returns the language of a sufficiently long English PDF, and `(None, 0.0)` for a scanned one. It never raises.

**Validation criteria.**
- `tests/unit/test_pdf_upload_security.py` (generate PDFs inline with pymupdf, or import the helpers by copying the minimal generator. **Do not import `tests/test_pdf/conftest.py` from `tests/unit`**):
  1. A valid PDF → `is_valid` with no warnings.
  2. `b"hello"` saved as `.pdf` → invalid, message contains `"%PDF"`.
  3. An encrypted PDF → invalid, message contains `"Password-protected"`.
  4. A scanned PDF (image only) → valid, one warning containing `"no text layer"`.
  5. A PDF with an unknown extension `.bin` → routed to `_validate_pdf_file` (valid).
  6. `LanguageDetector.detect_language_from_file` on a PDF with ~600 chars of English prose → `"English"` (or whatever name the detector returns for `en`; assert against `LanguageDetector`'s own mapping); scanned → `(None, 0.0)`.
- No upload-route test fixture exists in `tests/unit`, so test the validator directly with `SecureFileHandler().validate_and_save_file(data, "x.pdf")` (signature `(file_data: bytes, filename: str)`, as called in `security_routes.py`). The one-line `security_routes.py` change is covered by the Phase 7 manual smoke test (queue shows the PDF icon, which requires `file_type == "pdf"`).
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 6 — Auxiliary text consumers (cost, sample, NER, style, auto-prep)

**Goal.** Every feature that reads a document's text accepts PDF through the single `extract_pdf_text` helper.

**Files touched.**
- `src/utils/document_sampler.py` (modify)
- `src/api/blueprints/cost_routes.py` (modify)
- `src/api/blueprints/sample_routes.py` (modify)
- `src/core/auto_prep.py` (modify: docstring only, list `.pdf`)
- `tests/unit/test_auto_prep.py` (modify)
- `tests/unit/test_document_sampler.py` (modify)
- `tests/unit/style/test_style_extract_endpoint.py` (modify)
- `tests/unit/test_pdf_text_consumers.py` (create)

**Concrete deliverables.**
1. `document_sampler.py`: `RICH_EXTS = frozenset({'.epub', '.docx', '.pdf'})`; module docstring mentions PDF; `extract_full_text` gains `if ext == '.pdf': return _extract_pdf_full_text(file_data, hard_cap)`; new `_extract_pdf_full_text(file_data: bytes, hard_cap: int) -> str | None` returns `extract_pdf_text(file_data, hard_cap=hard_cap) or None` (lazy import). Glossary NER, style extraction and auto-prep pick this up with no further change, because they use `SUPPORTED_EXTS` / `RICH_EXTS` / `extract_full_text`. **Verify** that by reading `glossary_routes.py` (around the `_NER_SUPPORTED_EXTS` check), `custom_instruction_routes.py` (around `document_sampler.SUPPORTED_EXTS`) and `extract_samples_from_upload`. If any of them branches on `.epub`/`.docx` explicitly, add the `.pdf` branch the same way.
2. `cost_routes.py`: `if suffix == '.pdf': return _extract_pdf_text(file_path)` with `_extract_pdf_text(file_path: Path) -> str` → `extract_pdf_text(str(file_path))` (never raises; returns `''` on failure, logging a warning like `_extract_docx_text` when the result is empty).
3. `sample_routes.py`: `_extract_plain_text` gains `if ft == "pdf": return _extract_pdf_text(file_path)` with `_extract_pdf_text(file_path: str) -> str` returning `extract_pdf_text(file_path)`. Update the docstrings that list TXT/EPUB/DOCX. The existing "File is empty or unreadable" handling covers scanned PDFs.
4. Existing tests that used `.pdf` as the example of an **unsupported** extension switch to `.odt` (still unsupported everywhere):
   - `test_auto_prep.py::test_unsupported_extension_returns_empty`: `book.odt` with `b"PK\x03\x04 fake"`.
   - `test_document_sampler.py::test_unsupported_extension_returns_none_zero_zero`: `"notes.odt"`.
   - `test_style_extract_endpoint.py::test_5_unsupported_extension_is_400`: upload `(b"PK\x03\x04", "document.odt")`, assert `".odt" in body["error"] or "odt" in body["error"].lower()`, keep the `.txt` assertion, and update the docstring.
5. `tests/unit/test_pdf_text_consumers.py`: generate a 2-page text PDF inline with pymupdf and assert that:
   - `document_sampler.extract_full_text(bytes, "a.pdf")` is non-empty;
   - `auto_prep.extract_source_text(file_path=...)` is non-empty;
   - `cost_routes._extract_text_for_estimation(Path(path))` returns non-empty text;
   - `sample_routes._extract_plain_text(path, "pdf")` is non-empty;
   - a scanned PDF gives `None` / `''` respectively, never an exception.

**Dependency.** Parallelizable (after Phase 2; disjoint from Phases 4, 5, 7).

**Risk surface.** NONE. `sample_routes._validate_file` is a path-confinement boundary but is not modified.

**Contract.** For a text PDF, every listed consumer returns the same text as `extract_pdf_text`, possibly capped. For an unreadable PDF, each returns its existing "no text" value (`None` for the sampler, `''` elsewhere). No consumer raises because of a PDF.

**Validation criteria.** The tests above, plus gate `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.

**Ambiguity flag.** NONE.

---

### Phase 7 — Frontend and i18n

**Goal.** Let users pick and drop PDFs in every source-file input of the web UI, with reactive translated helper texts.

**Files touched.**
- `src/web/templates/translation_interface.html` (modify)
- `src/web/static/js/glossary/glossary-manager.js` (modify)
- `src/web/static/js/style/style-manager.js` (modify)
- `src/web/static/js/sample/sample-manager.js` (modify)
- `src/web/static/js/files/file-manager.js` (modify)
- `src/web/static/locales/{en,fr,es,de,zh-CN,ja,ko}/translation.json` (modify)
- `src/web/static/locales/{en,fr,es,de,zh-CN,ja,ko}/sample.json` (modify)
- `src/web/static/locales/{en,fr,es,de,zh-CN,ja,ko}/glossary.json` (modify)

**Concrete deliverables.**
1. `translation_interface.html`:
   - `accept=` gains `,.pdf` on `#fileInput`, `#sampleFileInput`, `#styleExtractFileInput`, `#ner-file-input`. **`#fileInputRefine` is NOT changed** (no PDF refine).
   - The fallback text inside the elements carrying `data-i18n="translation:drop_files_support"` and `data-i18n="sample:drop_file_support"` becomes the new English value; same for the `ner_intro` fallback block.
   - `<div class="ner-dropzone-hint">TXT · SRT · EPUB · DOCX</div>` → `TXT · SRT · EPUB · DOCX · PDF` (format names, not translated, as today).
2. JS: `NER_ACCEPTED_EXTS` (glossary-manager.js) and `EXTRACT_ACCEPTED_EXTS` (style-manager.js) gain `'pdf'`. `sample-manager.js::iconForFileType`: `if (ext === 'pdf') return 'picture_as_pdf';` before the `txt` line. `file-manager.js` file icon chain: add `file.file_type === 'pdf' ? 'picture_as_pdf' :` before the final fallback (match the surrounding ternary style). `supportsTTS` is NOT changed. `preflight-rules.js` `TAGGED_EXTENSIONS` is NOT changed (PDF carries no markup).
3. Locale values (modify existing keys; no new key; keep every HTML tag in `ner_intro` intact):

   | locale | `translation:drop_files_support` | `sample:drop_file_support` | `glossary:ner_intro` opening |
   |---|---|---|---|
   | en | `Support for TXT, EPUB, SRT, DOCX, and PDF` | `Support for TXT, EPUB, SRT, DOCX, and PDF` | `(TXT, SRT, EPUB, DOCX, PDF)` |
   | fr | `Prise en charge de TXT, EPUB, SRT, DOCX et PDF` | `Formats pris en charge : TXT, EPUB, SRT, DOCX et PDF` | `(TXT, SRT, EPUB, DOCX, PDF)` |
   | es | `Compatible con TXT, EPUB, SRT, DOCX y PDF` | `Compatible con TXT, EPUB, SRT, DOCX y PDF` | `(TXT, SRT, EPUB, DOCX, PDF)` |
   | de | `Unterstützung für TXT, EPUB, SRT, DOCX und PDF` | `Unterstützt TXT, EPUB, SRT, DOCX und PDF` | `(TXT, SRT, EPUB, DOCX, PDF)` |
   | zh-CN | `支持 TXT、EPUB、SRT、DOCX 和 PDF` | `支持 TXT、EPUB、SRT、DOCX 和 PDF` | `（TXT、SRT、EPUB、DOCX、PDF）` |
   | ja | `TXT、EPUB、SRT、DOCX、PDF に対応` | `TXT、EPUB、SRT、DOCX、PDF に対応` | `(TXT、SRT、EPUB、DOCX、PDF)` |
   | ko | `TXT, EPUB, SRT, DOCX, PDF 지원` | `TXT, EPUB, SRT, DOCX, PDF 지원` | `(TXT, SRT, EPUB, DOCX, PDF)` |

   For `ner_intro`, only the parenthesised format list changes; the rest of each value stays byte-identical.
4. The Plain Text Mode texts (`settings:option_plain_text_*`) are NOT changed.

**Dependency.** Parallelizable (after Phase 1; disjoint from Phases 4, 5, 6). The manual smoke test needs Phase 5 merged.

**Risk surface.** **Frontend i18n** (7-locale parity, `tests/test_frontend_i18n.py`).

**Contract.**
- The four inputs accept `.pdf`; the refine input does not.
- The three helper texts show PDF in every locale and update without reload when the UI language changes (they are `data-i18n` driven, so `applyToDOM` handles it).
- No new raw user-facing string is introduced in JS or HTML.

**Validation criteria.**
- `./venv/Scripts/python.exe -m pytest tests/test_frontend_i18n.py -q` green (key parity, no raw strings).
- Gate: `./venv/Scripts/python.exe -m pytest tests/unit tests/test_pdf -q` green.
- Manual smoke test (after Phase 5), reported in the PR: start the app (`./venv/Scripts/python.exe translation_api.py`), drop a text PDF in the main dropzone, check that the queue shows it with the PDF icon and the detected source language, run a translation with a configured provider, download the `.pdf`, and open it. Switch the UI language FR ↔ EN and check that the dropzone helper text updates without a reload. Drop the PDF on the NER and style-extraction dropzones: both accept it.

**Ambiguity flag.** NONE.

---

## Risks and open questions

1. **Extraction quality on real-world PDFs.** The thresholds in Phase 2 are deterministic and
   test-backed but tuned on synthetic fixtures. Expect follow-ups from users on multi-column
   papers, tables, footnotes, drop caps, and vertical Japanese. *Mitigation:* constants are
   module-level and named; the README states the limits; discussion #191's
   "pre-convert with pdf-craft / an OCR tool" stays the documented fallback for hard PDFs.
2. **Hyphenation false positives.** Rule 3(b) turns a genuine compound split at a line end
   (`well-` / `known`) into `wellknown`. This is the standard trade-off and is accepted.
3. **Output font look.** Latin text uses MuPDF's built-in Nimbus Roman/Sans; CJK uses Droid Sans
   Fallback (sans only). No font is bundled. Users who want a specific typeface are out of scope.
4. **Packaging.** PyMuPDF ships native binaries (~25 MB). The PyInstaller `hiddenimports` entry is
   added, but the Windows/macOS executables must be smoke-tested once by building them (not part
   of the pytest gate). Flag it in the PR description.
5. **AGPL coupling.** PyMuPDF is AGPL-3.0, compatible with the project licence. If the project
   ever relicenses under a permissive licence, this dependency has to be replaced (pypdf +
   reportlab), and the extractor/builder interfaces in `src/core/pdf/content.py` are the seam
   for that.
6. **Pre-existing quirk, not fixed here:** the DOCX branch of `translate_file()` does not pass
   `nim_api_key` to `create_llm_provider`; the PDF branch copies the DOCX branch, so it inherits
   the same behaviour. Fix both together in a separate change if NIM users hit it.
7. **Refine and TTS for PDF** are explicitly deferred. A future PDF refiner can reuse
   `extract_pdf_paragraphs` on our own output (clean single-column structure) plus `build_pdf`.

All phases have their ambiguity flag at NONE. No phase requires a human decision before delegation.
