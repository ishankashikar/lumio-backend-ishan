"""
ai/space_manager.py

Pure math utility — decides how content fits on a page before renderer draws.
No AI calls. No API endpoints. Called internally by renderer.py only.

JSON structure confirmed with Vaibhavi on 15/06/2026.
Key names are final — do not change without updating renderer.py too.

Returns:
  visible_columns → renderer draws these
  auto_hidden     → space manager hid these (shown in frontend banner)
  user_hidden     → user explicitly set operation="Hide" (never in banner)
  banner          → what frontend shows to inform user
  pagination      → rows per page, total pages
  truncation      → max chars per text column
  chart           → grouped chart data if too many points
  grouping        → validated grouping config
  page            → usable dimensions

NEVER auto-hides: Sum, GroupBy, Running Total columns.
These are core report data — report breaks without them.
"""

import math
from core.logger import get_logger

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

PAGE_SIZES = {
    "A4":     {"width": 794,  "height": 1123},
    "A3":     {"width": 1123, "height": 1587},
    "LETTER": {"width": 816,  "height": 1056},
}

HEADER_HEIGHT = 80
FOOTER_HEIGHT = 40

DEFAULT_MARGIN_LEFT  = 14
DEFAULT_MARGIN_RIGHT = 14

NEVER_HIDE_OPS = {"Sum", "GroupBy", "Running Total"}

HIDE_PRIORITY = {
    "Hide":          0,
    "Count":         1,
    "Display":       2,
    "Average":       3,
    "Running Total": 4,
    "Sum":           5,
    "GroupBy":       6,
}

DEFAULT_MAX_CHARS = 50
CHART_DENSE_LIMIT = 10
CHART_TOP_N       = 8


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _usable_dimensions(config: dict) -> dict:
    """
    Returns usable page dimensions after subtracting
    header, footer, and margins.

    Reads:
      config["page"]["size"]
      config["page"]["orientation"]
      config["page"]["marginLeft"]
      config["page"]["marginRight"]
      config["page"]["marginTop"]
      config["page"]["marginBottom"]
    """
    page        = config.get("page", {})
    size        = page.get("size", "A4").upper()
    orientation = page.get("orientation", "portrait").lower()
    margin_l    = page.get("marginLeft",   DEFAULT_MARGIN_LEFT)
    margin_r    = page.get("marginRight",  DEFAULT_MARGIN_RIGHT)
    margin_t    = page.get("marginTop",    20)
    margin_b    = page.get("marginBottom", 20)

    dims = PAGE_SIZES.get(size, PAGE_SIZES["A4"]).copy()

    if orientation == "landscape":
        dims["width"], dims["height"] = dims["height"], dims["width"]

    return {
        "total_width":   dims["width"],
        "total_height":  dims["height"],
        "usable_width":  max(dims["width"]  - margin_l - margin_r, 200),
        "usable_height": max(
            dims["height"] - HEADER_HEIGHT - FOOTER_HEIGHT - margin_t - margin_b,
            200
        ),
    }


def _row_height(font_size: int) -> int:
    """Row height estimate (1.5x line height + padding)."""
    return max(int(font_size * 1.5) + 10, 24)


def _col_min_width(operation: str, explicit_width: int, font_size: int) -> int:
    """
    Column width — uses explicit width from JSON if set,
    otherwise estimates from operation type.

    Reads: col["width"]
    """
    if explicit_width and explicit_width > 0:
        return explicit_width

    char_px = max(int(font_size * 0.65), 5)
    widths  = {
        "Sum":           char_px * 13,
        "Average":       char_px * 11,
        "Running Total": char_px * 13,
        "Count":         char_px * 9,
        "GroupBy":       char_px * 13,
        "Display":       char_px * 20,
        "Hide":          0,
    }
    return max(widths.get(operation, char_px * 11), 60)


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — CONVERT COLUMNS ARRAY → DICT + SEPARATE HIDDEN
# ═════════════════════════════════════════════════════════════════════════════

def _prepare_columns(config: dict) -> tuple[dict, list]:
    """
    Converts config["columns"] array → ordered dict keyed by column name.
    Separates operation="Hide" columns from active columns.

    Reads:
      config["columns"][n]["column"]
      config["columns"][n]["operation"]
      config["columns"][n]["label"]
      config["columns"][n]["order"]
      config["columns"][n]["width"]
      config["columns"][n]["align"]
      config["columns"][n]["format"]
      config["columns"][n]["maxChars"]
    """
    columns_array = config.get("columns", [])

    sorted_cols = sorted(
        columns_array,
        key=lambda c: c.get("order", 999)
    )

    active      = {}
    user_hidden = []

    for col in sorted_cols:
        col_name  = col.get("column", "")
        operation = col.get("operation", "Display")

        if not col_name:
            continue

        if operation == "Hide":
            user_hidden.append({
                "column": col_name,
                "label":  col.get("label", col_name),
                "type":   "user",
            })
        else:
            active[col_name] = col

    return active, user_hidden


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — PAGINATION
# ═════════════════════════════════════════════════════════════════════════════

def _compute_pagination(
    total_rows:    int,
    usable_height: int,
    font_size:     int,
) -> dict:
    """
    How many rows fit per page.
    Pure math — no overrides.
    """
    row_h       = _row_height(font_size)
    reserved    = row_h * 2
    available   = max(usable_height - reserved, row_h)
    rows_per_pg = max(int(available / row_h), 5)
    total_pages = math.ceil(total_rows / rows_per_pg) if rows_per_pg else 1

    return {
        "paginate":      total_rows > rows_per_pg,
        "rows_per_page": rows_per_pg,
        "total_pages":   total_pages,
        "row_height_px": row_h,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — COLUMN VISIBILITY
# ═════════════════════════════════════════════════════════════════════════════

def _compute_column_visibility(
    active_columns: dict,
    usable_width:   int,
    font_size:      int,
) -> tuple[dict, list]:
    """
    Determines which columns fit on the page.
    Returns (visible_columns, auto_hidden).

    Sum, GroupBy, Running Total → NEVER auto-hidden.
    Everything else → hidden by priority if page overflows.

    Reads: col["width"], col["operation"]
    """
    sorted_cols = sorted(
        active_columns.items(),
        key=lambda x: HIDE_PRIORITY.get(
            x[1].get("operation", "Display"), 2
        )
    )

    visible     = {}
    auto_hidden = []
    used_width  = 0

    for col_name, col_cfg in sorted_cols:
        operation      = col_cfg.get("operation", "Display")
        explicit_w     = col_cfg.get("width", 0) or 0
        min_w          = _col_min_width(operation, explicit_w, font_size)
        would_overflow = (used_width + min_w) > usable_width

        # Core columns — never auto-hide
        if operation in NEVER_HIDE_OPS:
            visible[col_name]  = {**col_cfg, "_min_width": min_w}
            used_width        += min_w
            continue

        if would_overflow:
            auto_hidden.append({
                "column": col_name,
                "label":  col_cfg.get("label", col_name),
                "reason": "Auto-hidden — exceeds available page width",
                "type":   "auto",
            })
            logger.info(
                f"[SPACE] Auto-hidden: {col_name} "
                f"({operation}) — width overflow"
            )
        else:
            visible[col_name]  = {**col_cfg, "_min_width": min_w}
            used_width        += min_w

    return visible, auto_hidden


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — TEXT TRUNCATION
# ═════════════════════════════════════════════════════════════════════════════

def _compute_truncation(
    visible_columns: dict,
    font_size:       int,
) -> dict:
    """
    Sets max character limit for Display text columns only.
    Never truncates numeric columns.

    Priority:
    1. col["maxChars"] set by user in UI
    2. Calculate from column width
    3. Fall back to DEFAULT_MAX_CHARS

    Reads: col["maxChars"], col["operation"]
    """
    truncation = {}

    for col_name, col_cfg in visible_columns.items():
        operation = col_cfg.get("operation", "Display")

        if operation != "Display":
            continue

        user_max = col_cfg.get("maxChars")
        if user_max and isinstance(user_max, int) and user_max > 0:
            truncation[col_name] = user_max
            continue

        min_w   = col_cfg.get("_min_width", 120)
        char_px = max(int(font_size * 0.65), 5)
        calc    = max(int(min_w / char_px), 15)

        truncation[col_name] = min(calc, DEFAULT_MAX_CHARS)

    return truncation


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — CHART GROUPING
# ═════════════════════════════════════════════════════════════════════════════

def _compute_chart_grouping(
    config:       dict,
    column_stats: dict,
) -> dict:
    """
    If chart has too many data points → group into Top N + Others.

    Reads:
      config["chart"]["enabled"]
      config["chart"]["xCol"]
      config["chart"]["type"]
      config["chart"]["aggFunc"]
    """
    chart = config.get("chart", {})

    if not chart.get("enabled", False):
        return {"apply_grouping": False}

    x_col  = chart.get("xCol", "")
    stats  = column_stats.get(x_col, {})
    groups = stats.get("group_counts", [])

    if len(groups) <= CHART_DENSE_LIMIT:
        return {"apply_grouping": False}

    top_grps   = groups[:CHART_TOP_N]
    others_sum = sum(g.get("count", 0) for g in groups[CHART_TOP_N:])

    logger.info(
        f"[SPACE] Chart grouped: "
        f"{len(groups)} points → Top {CHART_TOP_N} + Others"
    )

    return {
        "apply_grouping": True,
        "top_n":          CHART_TOP_N,
        "top_groups":     top_grps,
        "others_count":   others_sum,
        "others_label":   "Others",
        "original_count": len(groups),
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — GROUPBY VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def _validate_grouping(config: dict, visible_columns: dict) -> dict:
    """
    Validates grouping config against visible columns.
    If groupByColumn not visible → disable grouping silently.

    Reads:
      config["grouping"]["groupByColumn"]
      config["grouping"]["showSubtotals"]
      config["grouping"]["showGrandTotal"]
    """
    grouping    = config.get("grouping", {})
    group_col   = grouping.get("groupByColumn", "")
    subtotals   = grouping.get("showSubtotals",  True)
    grand_total = grouping.get("showGrandTotal", True)

    if not group_col:
        return {
            "enabled":        False,
            "groupByColumn":  "",
            "showSubtotals":  subtotals,
            "showGrandTotal": grand_total,
        }

    if group_col not in visible_columns:
        logger.warning(
            f"[SPACE] GroupBy column '{group_col}' "
            f"not in visible columns — grouping disabled"
        )
        return {
            "enabled":        False,
            "groupByColumn":  "",
            "showSubtotals":  subtotals,
            "showGrandTotal": grand_total,
        }

    return {
        "enabled":        True,
        "groupByColumn":  group_col,
        "showSubtotals":  subtotals,
        "showGrandTotal": grand_total,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 7 — BANNER
# ═════════════════════════════════════════════════════════════════════════════

def _build_banner(auto_hidden: list) -> dict:
    """
    Builds banner object for frontend.
    Only shown when space manager auto-hid something.
    User-hidden columns never appear here.

    Frontend shows:
    "2 columns auto-hidden for better view: Narration, Cheque No [+ Add back]"
    """
    if not auto_hidden:
        return {"show": False, "message": "", "columns": []}

    count   = len(auto_hidden)
    labels  = [c["label"] for c in auto_hidden]
    message = (
        f"{count} column{'s' if count > 1 else ''} "
        f"auto-hidden for better view: "
        f"{', '.join(labels)}"
    )

    return {
        "show":    True,
        "message": message,
        "columns": auto_hidden,
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def compute_space_decisions(
    config:       dict,
    total_rows:   int,
    column_stats: dict = None,
) -> dict:
    """
    Main entry point. Called by renderer.py before drawing anything.

    Args:
        config       : full report JSON (confirmed structure from Vaibhavi)
        total_rows   : total records in this report
        column_stats : pre-computed stats (needed for chart grouping)

    Returns:
        {
            visible_columns : dict
            auto_hidden     : list
            user_hidden     : list
            banner          : dict
            pagination      : dict
            truncation      : dict
            chart           : dict
            grouping        : dict
            page            : dict
        }
    """
    font_size = config.get("style", {}).get("fontSize", 13)

    # Page dimensions
    page = _usable_dimensions(config)

    # Step 1 — Convert + separate hidden
    active_cols, user_hidden = _prepare_columns(config)

    # Step 2 — Pagination
    pagination = _compute_pagination(
        total_rows    = total_rows,
        usable_height = page["usable_height"],
        font_size     = font_size,
    )

    # Step 3 — Column visibility
    visible_cols, auto_hidden = _compute_column_visibility(
        active_columns = active_cols,
        usable_width   = page["usable_width"],
        font_size      = font_size,
    )

    # Step 4 — Text truncation
    truncation = _compute_truncation(
        visible_columns = visible_cols,
        font_size       = font_size,
    )

    # Step 5 — Chart grouping
    chart = _compute_chart_grouping(
        config       = config,
        column_stats = column_stats or {},
    )

    # Step 6 — Groupby validation
    grouping = _validate_grouping(config, visible_cols)

    # Step 7 — Banner
    banner = _build_banner(auto_hidden)

    # Clean internal keys before returning
    clean_visible = {
        col: {k: v for k, v in cfg.items() if k != "_min_width"}
        for col, cfg in visible_cols.items()
    }

    logger.info(
        f"[SPACE] template={config.get('template', 'tabular')} | "
        f"page={config.get('page', {}).get('size', 'A4')} | "
        f"font={font_size} | "
        f"rows={total_rows} → "
        f"{pagination['rows_per_page']}/page × "
        f"{pagination['total_pages']} pages | "
        f"cols: visible={len(clean_visible)} "
        f"auto_hidden={len(auto_hidden)} "
        f"user_hidden={len(user_hidden)}"
    )

    return {
        "visible_columns": clean_visible,
        "auto_hidden":     auto_hidden,
        "user_hidden":     user_hidden,
        "banner":          banner,
        "pagination":      pagination,
        "truncation":      truncation,
        "chart":           chart,
        "grouping":        grouping,
        "page":            page,
    }