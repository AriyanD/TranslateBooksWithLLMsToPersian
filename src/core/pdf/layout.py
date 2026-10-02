"""
Layout detection on a PDF page: ruled tables and filled background boxes.

detect_tables finds two kinds of tables:
- fully ruled tables, through PyMuPDF's own page.find_tables();
- "rule grids": tables drawn with horizontal rules only, split per column
  (the per-cell border-bottom that headless Chrome / Skia prints as one thin
  filled rectangle per column segment). Consecutive separator rows sharing
  the same column boundaries form a grid; its top comes from a header fill
  (one non-white filled rectangle per header cell) or, for a continuation
  table, from the text lines just above the first rule. The grid is then
  extracted with find_tables() in explicit-lines mode.

detect_boxes finds filled rectangles drawn behind text (callouts), with an
optional thin bar on their left edge. White fills, rules, page-sized
backgrounds and fills inside detected tables are ignored.

Known accepted limits (these layouts fall back to plain paragraphs):
- tables drawn with neither vertical lines nor per-column rules (borderless
  tables, LaTeX booktabs-style full-width rules);
- "border-top" tables whose last row sits below the last rule;
- column spans;
- badge fills inside table cells.

The input is untrusted: both public functions never raise. Any failure is
logged at debug level and yields an empty list. Output is deterministic for
a given page.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import pymupdf

from .styling import fill_to_hex

logger = logging.getLogger(__name__)

# find_tables() prints a one-off advisory about an optional add-on package to
# stdout; the extraction pipeline must stay silent.
if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()

BBox = Tuple[float, float, float, float]


# --- Tuning constants --------------------------------------------------------

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

_SINGLE_SEPARATOR_ROW_RATIO = 0.05   # assumed row height of a one-separator run, fraction of page height
_HORIZONTAL_LINE_MAX_DY = 0.5        # pt: a stroked line flatter than this is horizontal
_MIN_FILL_OPACITY = 0.5


# --- Results -----------------------------------------------------------------


@dataclass
class DetectedTable:
    """A table found on a page; cells and cell_bboxes have the same shape."""
    bbox: BBox
    cells: List[List[Optional[str]]]                                # Table.extract() output
    cell_bboxes: List[List[Optional[BBox]]]
    header: bool
    header_fill: Optional[str]                                      # "#rrggbb" when header is True
    column_widths: List[float]                                      # relative, sum 1.0


@dataclass
class DetectedBox:
    """A filled background rectangle, with the colour of its left bar if any."""
    rect: BBox
    background: str                                                 # "#rrggbb"
    border_left: Optional[str]


@dataclass
class _Fill:
    rect: BBox
    fill: Tuple[float, float, float]
    opacity: float
    order: int                      # position among the page's filled rects (drawing order)


@dataclass
class _Separator:
    y: float
    boundaries: List[float]


# --- Drawing helpers ---------------------------------------------------------


def _as_bbox(value) -> Optional[BBox]:
    """Normalise a Rect-like value to an ordered (x0, y0, x1, y1) float tuple."""
    try:
        x0, y0, x1, y1 = (float(value[i]) for i in range(4))
    except (TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _as_rgb(value) -> Optional[Tuple[float, float, float]]:
    """Return an RGB float triple from a drawing colour (grey expanded), or None."""
    if value is None:
        return None
    try:
        channels = tuple(float(c) for c in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(c) for c in channels):
        return None
    if len(channels) == 1:
        return (channels[0],) * 3
    if len(channels) == 3:
        return channels
    return None


def _is_white(fill: Sequence[float]) -> bool:
    return all(channel >= WHITE_MIN for channel in fill)


def _width(box: BBox) -> float:
    return box[2] - box[0]


def _height(box: BBox) -> float:
    return box[3] - box[1]


def _items(path: dict) -> list:
    items = path.get("items")
    return items if isinstance(items, (list, tuple)) else []


def _opacity(path: dict) -> float:
    value = path.get("fill_opacity")
    try:
        return 1.0 if value is None else float(value)
    except (TypeError, ValueError):
        return 1.0


def _filled_rects(drawings: list) -> List[_Fill]:
    """Every "re" item of a filled path, in drawing order (no colour or size filter)."""
    result = []
    for path in drawings:
        if not isinstance(path, dict):
            continue
        fill = _as_rgb(path.get("fill"))
        if fill is None:
            continue
        opacity = _opacity(path)
        for item in _items(path):
            if not isinstance(item, (list, tuple)) or len(item) < 2 or item[0] != "re":
                continue
            box = _as_bbox(item[1])
            if box is not None:
                result.append(_Fill(box, fill, opacity, len(result)))
    return result


def _fill_rects(filled: List[_Fill]) -> List[_Fill]:
    """Opaque, non-white filled rectangles taller than a rule (step 3)."""
    return [
        f for f in filled
        if not _is_white(f.fill) and f.opacity >= _MIN_FILL_OPACITY and _height(f.rect) > RULE_MAX_THICKNESS
    ]


def _rule_segments(drawings: list) -> List[Tuple[float, float, float]]:
    """Horizontal rules as (x0, x1, y): thin filled rects and stroked lines (step 2)."""
    segments = []
    for path in drawings:
        if not isinstance(path, dict):
            continue
        filled = path.get("fill") is not None
        stroked_only = path.get("color") is not None and not filled
        for item in _items(path):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            if item[0] == "re" and filled:
                box = _as_bbox(item[1])
                if box and _height(box) <= RULE_MAX_THICKNESS and _width(box) >= RULE_MIN_LENGTH:
                    segments.append((box[0], box[2], (box[1] + box[3]) / 2))
            elif item[0] == "l" and stroked_only and len(item) >= 3:
                try:
                    ax, ay = float(item[1][0]), float(item[1][1])
                    bx, by = float(item[2][0]), float(item[2][1])
                except (TypeError, ValueError, IndexError):
                    continue
                if abs(by - ay) <= _HORIZONTAL_LINE_MAX_DY and abs(bx - ax) >= RULE_MIN_LENGTH:
                    segments.append((min(ax, bx), max(ax, bx), (ay + by) / 2))
    return segments


def _x_coverage(intervals: List[Tuple[float, float]], x0: float, x1: float) -> float:
    """Length of the union of the intervals, clipped to [x0, x1]."""
    clipped = sorted((max(a, x0), min(b, x1)) for a, b in intervals if min(b, x1) > max(a, x0))
    total, current_start, current_end = 0.0, None, None
    for start, end in clipped:
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += current_end - current_start
    return total


def _normalized_widths(widths: List[float]) -> List[float]:
    widths = [max(0.0, w) for w in widths]
    total = sum(widths)
    if not widths:
        return []
    if total <= 0:
        return [1.0 / len(widths)] * len(widths)
    return [w / total for w in widths]


def _intersects(a: BBox, b: BBox) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _contained(inner: BBox, outer: BBox, tolerance: float) -> bool:
    return (
        inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance
    )


# --- Tables: shared extraction ------------------------------------------------


def _table_grid(table) -> Optional[Tuple[BBox, list, list]]:
    """(bbox, cells, cell_bboxes) of a PyMuPDF table, or None when shapes disagree."""
    bbox = _as_bbox(table.bbox)
    cells = [list(row) for row in table.extract()]
    cell_bboxes = [[_as_bbox(cell) if cell is not None else None for cell in row.cells] for row in table.rows]
    if bbox is None or not cells or [len(r) for r in cells] != [len(r) for r in cell_bboxes]:
        return None
    return bbox, cells, cell_bboxes


# --- Tables: step 1 (fully ruled tables) --------------------------------------


def _ruled_column_widths(cell_bboxes: List[List[Optional[BBox]]], col_count: int) -> List[float]:
    """Relative widths from the row with the most cells; gaps filled from other rows."""
    best = max(cell_bboxes, key=lambda row: sum(1 for cell in row if cell is not None))
    candidates = [best] + cell_bboxes
    widths = []
    for column in range(col_count):
        width = next(
            (_width(row[column]) for row in candidates if column < len(row) and row[column] is not None),
            0.0,
        )
        widths.append(width)
    return _normalized_widths(widths)


def _ruled_header(row_box: BBox, fills: List[_Fill]) -> Tuple[bool, Optional[str]]:
    """Header when non-white fills inside row 0 cover HEADER_COVERAGE of its width."""
    inside = [f for f in fills if _contained(f.rect, row_box, BOUNDARY_TOLERANCE)]
    width = _width(row_box)
    if not inside or width <= 0:
        return False, None
    covered = _x_coverage([(f.rect[0], f.rect[2]) for f in inside], row_box[0], row_box[2])
    if covered < HEADER_COVERAGE * width:
        return False, None
    widest = max(inside, key=lambda f: _width(f.rect))
    return True, fill_to_hex(widest.fill)


def _ruled_tables(page, fills: List[_Fill]) -> List[DetectedTable]:
    result = []
    for table in page.find_tables().tables:
        if table.row_count < 2 or table.col_count < 2:
            continue
        grid = _table_grid(table)
        if grid is None:
            continue
        bbox, cells, cell_bboxes = grid
        row_box = _as_bbox(table.rows[0].bbox)
        header, header_fill = _ruled_header(row_box, fills) if row_box else (False, None)
        widths = _ruled_column_widths(cell_bboxes, len(cell_bboxes[0]))
        result.append(DetectedTable(bbox, cells, cell_bboxes, header, header_fill, widths))
    return result


# --- Tables: steps 2-7 (rule grids) --------------------------------------------


def _separator_rows(segments: List[Tuple[float, float, float]]) -> List[_Separator]:
    """Group rule segments by y; keep groups with at least two columns (step 2)."""
    groups: List[List[Tuple[float, float, float]]] = []
    for segment in sorted(segments, key=lambda s: (s[2], s[0], s[1])):
        if groups and segment[2] - groups[-1][0][2] <= RULE_Y_TOLERANCE:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    separators = []
    for group in groups:
        boundaries: List[float] = []
        for value in sorted(v for x0, x1, _ in group for v in (x0, x1)):
            if not boundaries or value - boundaries[-1] >= BOUNDARY_TOLERANCE:
                boundaries.append(value)
        if len(boundaries) >= 3:
            separators.append(_Separator(sum(s[2] for s in group) / len(group), boundaries))
    return separators


def _same_columns(a: List[float], b: List[float]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= BOUNDARY_TOLERANCE for x, y in zip(a, b))


def _runs(separators: List[_Separator], page_height: float) -> List[List[_Separator]]:
    """Consecutive separators with the same column boundaries (step 4)."""
    runs: List[List[_Separator]] = []
    max_gap = MAX_ROW_HEIGHT_RATIO * page_height
    for separator in separators:
        if (
            runs
            and _same_columns(separator.boundaries, runs[-1][0].boundaries)
            and separator.y - runs[-1][-1].y <= max_gap
        ):
            runs[-1].append(separator)
        else:
            runs.append([separator])
    return runs


def _run_top(
    run: List[_Separator],
    fills: List[_Fill],
    line_bboxes: List[BBox],
    page_height: float,
) -> Tuple[float, bool, Optional[str]]:
    """(top, header, header_fill) of a run: header fills first, else the text above (step 5)."""
    first = run[0]
    x0, x1 = first.boundaries[0], first.boundaries[-1]
    max_gap = MAX_ROW_HEIGHT_RATIO * page_height
    header_fills = [
        f for f in fills
        if abs(f.rect[3] - first.y) <= RULE_Y_TOLERANCE + 1.0
        and first.y - f.rect[1] <= max_gap
        and f.rect[0] >= x0 - BOUNDARY_TOLERANCE
        and f.rect[2] <= x1 + BOUNDARY_TOLERANCE
    ]
    if header_fills:
        covered = _x_coverage([(f.rect[0], f.rect[2]) for f in header_fills], x0, x1)
        if covered >= HEADER_COVERAGE * (x1 - x0):
            widest = max(header_fills, key=lambda f: _width(f.rect))
            return min(f.rect[1] for f in header_fills), True, fill_to_hex(widest.fill)
    gaps = [b.y - a.y for a, b in zip(run, run[1:])]
    h_max = max(gaps) if gaps else _SINGLE_SEPARATOR_ROW_RATIO * page_height
    tops = [
        box[1] for box in line_bboxes
        if x0 <= (box[0] + box[2]) / 2 <= x1
        and box[3] <= first.y + 1
        and box[1] >= first.y - h_max
    ]
    return (min(tops) - 1 if tops else first.y), False, None


def _row_boundaries(top: float, run: List[_Separator]) -> List[float]:
    boundaries: List[float] = []
    for value in sorted([top] + [s.y for s in run]):
        if not boundaries or value - boundaries[-1] > RULE_Y_TOLERANCE:
            boundaries.append(value)
    return boundaries


def _extract_grid(page, columns: List[float], rows: List[float]):
    """Extract a grid with explicit lines (step 7); None when PyMuPDF finds no table."""
    clip = pymupdf.Rect(columns[0] - 1, rows[0] - 1, columns[-1] + 1, rows[-1] + 1)
    tables = page.find_tables(
        clip=clip,
        vertical_strategy="explicit",
        horizontal_strategy="explicit",
        vertical_lines=columns,
        horizontal_lines=rows,
    ).tables
    return tables[0] if tables else None


def _rule_grids(page, drawings: list, fills: List[_Fill], line_bboxes: List[BBox]) -> List[DetectedTable]:
    page_height = float(page.rect.height)
    result = []
    for run in _runs(_separator_rows(_rule_segments(drawings)), page_height):
        top, header, header_fill = _run_top(run, fills, line_bboxes, page_height)
        if not (header or len(run) >= 2):
            continue
        rows = _row_boundaries(top, run)
        if len(rows) < 2:
            continue
        columns = run[0].boundaries
        table = _extract_grid(page, columns, rows)
        grid = _table_grid(table) if table is not None else None
        if grid is None:
            continue
        bbox, cells, cell_bboxes = grid
        widths = _normalized_widths([b - a for a, b in zip(columns, columns[1:])])
        result.append(DetectedTable(bbox, cells, cell_bboxes, header, header_fill, widths))
    return result


# --- Public API ----------------------------------------------------------------


def detect_tables(page, line_bboxes: List[BBox]) -> List[DetectedTable]:
    """
    Detect the ruled tables of a page (fully ruled tables, then rule grids).

    line_bboxes are the bboxes of the page's non-blank text lines; they locate
    the first row of a headerless grid. Returns tables sorted by (y0, x0).
    Never raises: any failure yields [].
    """
    try:
        drawings = page.get_drawings() or []
        fills = _fill_rects(_filled_rects(drawings))
        lines = [box for box in (_as_bbox(b) for b in (line_bboxes or [])) if box is not None]
        ruled = _ruled_tables(page, fills)
        grids = [
            grid for grid in _rule_grids(page, drawings, fills, lines)
            if not any(_intersects(grid.bbox, table.bbox) for table in ruled)
        ]
        return sorted(ruled + grids, key=lambda t: (t.bbox[1], t.bbox[0]))
    except Exception as exc:  # untrusted input: never fail the extraction
        logger.debug("Table detection skipped on a page: %s", exc)
        return []


def _left_bar(box: _Fill, bars: List[_Fill]) -> Optional[str]:
    """Colour of the first bar (drawing order) sitting on the box's left edge (step 3)."""
    rect = box.rect
    height = _height(rect)
    for bar in bars:
        if bar.order == box.order:
            continue
        overlap = min(rect[3], bar.rect[3]) - max(rect[1], bar.rect[1])
        if (
            abs(bar.rect[0] - rect[0]) <= BAR_EDGE_TOLERANCE
            and _height(bar.rect) >= 0.5 * height
            and overlap >= 0.8 * height
        ):
            return fill_to_hex(bar.fill)
    return None


def detect_boxes(page, exclude: List[BBox]) -> List[DetectedBox]:
    """
    Detect filled background boxes of a page, with their optional left bar.

    Boxes contained in an exclude rect (e.g. a detected table) are skipped.
    Returns boxes sorted by (area, y0, x0), smallest first. Never raises:
    any failure yields [].
    """
    try:
        drawings = page.get_drawings() or []
        page_area = float(page.rect.width) * float(page.rect.height)
        excluded = [box for box in (_as_bbox(b) for b in (exclude or [])) if box is not None]
        filled = _filled_rects(drawings)
        bars = [
            f for f in filled
            if not _is_white(f.fill) and _width(f.rect) <= BAR_MAX_WIDTH and _height(f.rect) >= MIN_BOX_SIDE
        ]
        result: List[DetectedBox] = []
        seen = set()
        for candidate in _fill_rects(filled):
            rect = candidate.rect
            if _width(rect) < MIN_BOX_SIDE or _height(rect) < MIN_BOX_SIDE:
                continue
            if _width(rect) * _height(rect) > MAX_BOX_AREA_RATIO * page_area:
                continue
            if any(_contained(rect, other, CONTAIN_TOLERANCE) for other in excluded):
                continue
            key = tuple(round(v, 1) for v in rect)
            if key in seen:
                continue
            seen.add(key)
            result.append(DetectedBox(rect, fill_to_hex(candidate.fill), _left_bar(candidate, bars)))
        return sorted(result, key=lambda b: (_width(b.rect) * _height(b.rect), b.rect[1], b.rect[0]))
    except Exception as exc:  # untrusted input: never fail the extraction
        logger.debug("Box detection skipped on a page: %s", exc)
        return []
