"""
ai/renderer.py

Orchestrates the full render pipeline:
1. Space manager   → decides layout, visible columns, pagination
2. Compute service → applies operations, calculates totals/groups
3. Merges results  → packages clean structured response for frontend

Frontend uses this response to:
→ render React JSX preview
→ generate jsPDF export
→ generate XLSX export

renderer.py never generates HTML, PDF, or Excel itself.
It only prepares structured data — frontend handles all visual output.
"""

from services.compute_service import compute
from ai.space_manager         import compute_space_decisions
from core.logger              import get_logger

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _inject_per_row(
    visible_rows: list[dict],
    per_row:      dict,
    column_order: list[str],
) -> list[dict]:
    """
    Injects per-row computed values into each row dict.
    Handles: Running Total, Rank, % of Total.

    These operations produce one value per row —
    compute_service returns them as lists, we inject them back into rows.
    """
    if not per_row:
        return visible_rows

    result = []
    for i, row in enumerate(visible_rows):
        new_row = dict(row)
        for col_name, values in per_row.items():
            if col_name in column_order and i < len(values):
                new_row[col_name] = values[i]
        result.append(new_row)

    return result


def _apply_truncation(
    visible_rows: list[dict],
    truncation:   dict,
) -> list[dict]:
    """
    Applies text truncation to Display columns only.
    Numeric columns are never truncated.
    truncation = {col_name: max_chars} from space manager.
    """
    if not truncation:
        return visible_rows

    result = []
    for row in visible_rows:
        new_row = {}
        for col_name, value in row.items():
            max_chars = truncation.get(col_name)
            if max_chars and value is not None:
                str_val             = str(value)
                new_row[col_name]   = (
                    str_val[:max_chars] + "..."
                    if len(str_val) > max_chars
                    else str_val
                )
            else:
                new_row[col_name] = value
        result.append(new_row)

    return result


def _filter_to_visible(
    data:         dict,
    visible_cols: set,
) -> dict:
    """Removes hidden columns from a flat dict (grand_totals etc.)."""
    return {
        col: val
        for col, val in data.items()
        if col in visible_cols
    }


def _filter_groups_to_visible(
    groups:       list[dict],
    visible_cols: set,
) -> list[dict]:
    """
    Removes hidden columns from group rows and subtotals.
    Groups come from compute_service with all columns —
    we strip hidden ones so frontend never sees them.
    """
    if not groups:
        return []

    result = []
    for group in groups:
        result.append({
            "key":   group["key"],
            "count": group["count"],
            "rows": [
                {
                    col: val
                    for col, val in row.items()
                    if col in visible_cols
                }
                for row in group.get("rows", [])
            ],
            "subtotals": {
                col: val
                for col, val in group.get("subtotals", {}).items()
                if col in visible_cols
            },
        })

    return result


def _paginate(
    rows:          list[dict],
    rows_per_page: int,
    page:          int,
) -> dict:
    """
    Returns one page of rows.
    Page is 1-indexed.
    Never crashes — clamps page to valid range.
    """
    total_rows  = len(rows)
    total_pages = max(1, -(-total_rows // rows_per_page))  # ceiling division
    page        = max(1, min(page, total_pages))            # clamp to valid range

    start = (page - 1) * rows_per_page
    end   = start + rows_per_page

    return {
        "rows":          rows[start:end],
        "page":          page,
        "total_pages":   total_pages,
        "total_rows":    total_rows,
        "rows_per_page": rows_per_page,
        "has_next":      page < total_pages,
        "has_prev":      page > 1,
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def render(
    config:       dict,
    rows:         list[list],
    column_stats: dict = None,
    page:         int  = 1,
) -> dict:
    """
    Main entry point. Called by API router.

    Args:
        config       : full report JSON (confirmed structure from Vaibhavi)
        rows         : raw rows from Oracle (list of lists, ordered by columns)
        column_stats : pre-computed stats from cache_service (for space manager)
        page         : page number requested by frontend (1-indexed)

    Returns complete structured response for frontend:
        {
            columns      : visible column configs (frontend uses for headers)
            rows         : paginated rows with all computed values
            grand_totals : {col: value} for Sum/Average/Count/Running Total
            groups       : [{key, rows, subtotals, count}] for GroupBy template
            pagination   : page info
            banner       : space manager notification (hidden columns)
            chart        : chart grouping decisions
            grouping     : validated grouping config
            style        : fonts, colors, borders
            bank         : bank header info
            watermark    : watermark config
            template     : tabular/groupby/hierarchical/comparative/drilldown
            page_config  : margins, size, orientation
        }
    """
    # ── Step 1: Space manager ──────────────────────────────────────────────
    # Decides which columns fit, pagination, truncation, chart grouping
    space = compute_space_decisions(
        config       = config,
        total_rows   = len(rows),
        column_stats = column_stats or {},
    )

    visible_col_names = set(space["visible_columns"].keys())
    rows_per_page     = space["pagination"]["rows_per_page"]

    # ── Step 2: Build filtered config for compute ──────────────────────────
    # Only pass visible columns to compute_service
    # Hidden columns (user or auto) are excluded here — never computed
    filtered_columns = [
        col for col in config.get("columns", [])
        if col.get("column") in visible_col_names
    ]
    filtered_config = {
        **config,
        "columns": filtered_columns,
    }

    # ── Step 3: Compute operations ─────────────────────────────────────────
    # Applies Sum, Average, GroupBy, Running Total etc. from JSON config
    computed = compute(
        config = filtered_config,
        rows   = rows,
    )

    # ── Step 4: Inject per-row values into rows ────────────────────────────
    # Running Total, Rank, % of Total are computed as lists
    # We inject them back into each row dict
    visible_rows = _inject_per_row(
        visible_rows = computed["visible_rows"],
        per_row      = computed["per_row"],
        column_order = computed["column_order"],
    )

    # ── Step 5: Apply text truncation ─────────────────────────────────────
    visible_rows = _apply_truncation(
        visible_rows = visible_rows,
        truncation   = space["truncation"],
    )

    # ── Step 6: Filter groups and totals to visible columns ────────────────
    groups = _filter_groups_to_visible(
        groups       = computed["groups"],
        visible_cols = visible_col_names,
    )
    grand_totals = _filter_to_visible(
        data         = computed["grand_totals"],
        visible_cols = visible_col_names,
    )

    # ── Step 7: Paginate ───────────────────────────────────────────────────
    paginated = _paginate(
        rows          = visible_rows,
        rows_per_page = rows_per_page,
        page          = page,
    )

    logger.info(
        f"[RENDERER] template={config.get('template', 'tabular')} | "
        f"total_rows={len(rows)} | "
        f"visible_cols={len(visible_col_names)} | "
        f"page={paginated['page']}/{paginated['total_pages']} | "
        f"groups={len(groups)} | "
        f"banner={space['banner']['show']}"
    )

    return {
        # Column metadata — frontend uses for table headers
        "columns":      filtered_columns,

        # Paginated rows with all computed values injected
        "rows":         paginated["rows"],

        # Totals row — shown at bottom of table
        "grand_totals": grand_totals,

        # GroupBy structure — used when template = "groupby"
        "groups":       groups,

        # Pagination info — frontend uses for page controls
        "pagination": {
            "page":          paginated["page"],
            "total_pages":   paginated["total_pages"],
            "total_rows":    paginated["total_rows"],
            "rows_per_page": paginated["rows_per_page"],
            "has_next":      paginated["has_next"],
            "has_prev":      paginated["has_prev"],
        },

        # Space manager decisions — frontend shows banner if needed
        "banner":      space["banner"],
        "chart":       space["chart"],
        "grouping":    space["grouping"],

        # Config blocks — frontend uses for visual rendering
        "style":       config.get("style",     {}),
        "bank":        config.get("bank",      {}),
        "watermark":   config.get("watermark", {}),
        "template":    config.get("template",  "tabular"),
        "page_config": config.get("page",      {}),
    }