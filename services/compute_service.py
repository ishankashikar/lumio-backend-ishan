"""
services/compute_service.py

Applies column operations from JSON config to raw Oracle rows.
Operations are decided by column intelligence + confirmed by user.
This service trusts the JSON completely — no re-validation.

Only safety net: non-numeric values are skipped for numeric operations.
App never crashes on bad data — just skips and logs.

Supports all operations:
    Sum, Average, Count, GroupBy, Display,
    Hide, Running Total, % of Total, Rank
"""

from core.logger import get_logger

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _to_float(value) -> float | None:
    """
    Safely converts value to float.
    Returns None if conversion fails — never crashes.
    """
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _get_col_index(columns: list[dict]) -> dict:
    """
    Builds column name → index map from config columns array.
    Reads: col["column"] from JSON.
    """
    return {
        col["column"]: i
        for i, col in enumerate(columns)
    }


def _get_col_value(row: list, col_index: dict, col_name: str):
    """Safely gets value from row by column name."""
    idx = col_index.get(col_name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


# ═════════════════════════════════════════════════════════════════════════════
# OPERATION HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def _compute_sum(values: list) -> float:
    """Sum all numeric values. Skips non-numeric silently."""
    total = 0.0
    for v in values:
        n = _to_float(v)
        if n is not None:
            total += n
    return round(total, 2)


def _compute_average(values: list) -> float | None:
    """Average of all numeric values. Returns None if no valid values."""
    numeric = [_to_float(v) for v in values if _to_float(v) is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def _compute_count(values: list) -> int:
    """Count non-null values."""
    return sum(1 for v in values if v is not None and str(v).strip() != "")


def _compute_percent_of_total(values: list) -> list[float | None]:
    """
    Each value as % of column total.
    Returns list same length as input.
    """
    total = _compute_sum(values)
    if total == 0:
        return [None] * len(values)

    result = []
    for v in values:
        n = _to_float(v)
        if n is not None:
            result.append(round((n / total) * 100, 2))
        else:
            result.append(None)
    return result


def _compute_rank(values: list) -> list[int | None]:
    """
    Rank rows by value descending (highest = rank 1).
    Returns list same length as input.
    """
    indexed = []
    for i, v in enumerate(values):
        n = _to_float(v)
        indexed.append((i, n))

    # Sort by value descending, None goes last
    sorted_vals = sorted(
        indexed,
        key=lambda x: x[1] if x[1] is not None else float("-inf"),
        reverse=True
    )

    ranks = [None] * len(values)
    for rank, (original_idx, val) in enumerate(sorted_vals, start=1):
        if val is not None:
            ranks[original_idx] = rank

    return ranks


def _compute_running_total(values: list) -> list[float | None]:
    """
    Cumulative running sum row by row.
    Non-numeric values reset to None but don't break the sequence.
    """
    result  = []
    running = 0.0

    for v in values:
        n = _to_float(v)
        if n is not None:
            running += n
            result.append(round(running, 2))
        else:
            result.append(None)

    return result


# ═════════════════════════════════════════════════════════════════════════════
# GROUPBY HANDLER
# ═════════════════════════════════════════════════════════════════════════════

def _compute_groupby(
    rows:        list[list],
    col_index:   dict,
    group_col:   str,
    columns:     list[dict],
) -> list[dict]:
    """
    Groups rows by group_col value.
    For each group computes subtotals for all Sum/Average/Count columns.

    Returns list of:
    {
        key       : group value (e.g. "001")
        rows      : list of raw rows in this group
        subtotals : {col_name: value} for numeric columns
        count     : number of rows in group
    }
    """
    # Build ordered groups (preserves first-seen order)
    groups = {}

    for row in rows:
        key = _get_col_value(row, col_index, group_col)
        key = str(key).strip() if key is not None else "—"

        if key not in groups:
            groups[key] = []
        groups[key].append(row)

    # Compute subtotals for each group
    result = []

    for key, group_rows in groups.items():
        subtotals = {}

        for col in columns:
            col_name  = col.get("column", "")
            operation = col.get("operation", "Display")

            if operation in ("Sum", "Running Total"):
                vals = [
                    _get_col_value(r, col_index, col_name)
                    for r in group_rows
                ]
                subtotals[col_name] = _compute_sum(vals)

            elif operation == "Average":
                vals = [
                    _get_col_value(r, col_index, col_name)
                    for r in group_rows
                ]
                subtotals[col_name] = _compute_average(vals)

            elif operation == "Count":
                vals = [
                    _get_col_value(r, col_index, col_name)
                    for r in group_rows
                ]
                subtotals[col_name] = _compute_count(vals)

        result.append({
            "key":       key,
            "rows":      group_rows,
            "subtotals": subtotals,
            "count":     len(group_rows),
        })

    return result


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def compute(
    config: dict,
    rows:   list[list],
) -> dict:
    """
    Main entry point. Called after column intelligence confirms operations.
    Applies all operations dynamically from JSON config.
    Trusts JSON completely — user already confirmed operations.

    Args:
        config : full report JSON (confirmed structure from Vaibhavi)
        rows   : raw rows from Oracle (list of lists, ordered by columns)

    Returns:
        {
            visible_rows    : rows with hidden columns removed
            grand_totals    : {col: value} for Sum/Average/Count
            groups          : [{key, rows, subtotals, count}] for GroupBy
            per_row         : {col: [values]} for Running Total/Rank/% of Total
            column_order    : [col_names] in display order
            total_rows      : int
        }

    Reads from confirmed JSON keys:
        config["columns"][n]["column"]
        config["columns"][n]["operation"]
        config["columns"][n]["order"]
    """
    columns   = config.get("columns", [])
    grouping  = config.get("grouping", {})
    group_col = grouping.get("groupByColumn", "")

    # Sort columns by order
    columns = sorted(columns, key=lambda c: c.get("order", 999))

    # Build column index map
    col_index = _get_col_index(columns)

    # Visible columns only (exclude operation="Hide")
    visible_cols = [
        col for col in columns
        if col.get("operation", "Display") != "Hide"
    ]
    column_order = [col["column"] for col in visible_cols]

    # ── Grand totals, per-row calculations ────────────────────────────────────
    grand_totals = {}
    per_row      = {}

    for col in visible_cols:
        col_name  = col.get("column", "")
        operation = col.get("operation", "Display")

        # Extract all values for this column
        all_values = [
            _get_col_value(row, col_index, col_name)
            for row in rows
        ]

        if operation == "Sum":
            grand_totals[col_name] = _compute_sum(all_values)

        elif operation == "Average":
            grand_totals[col_name] = _compute_average(all_values)

        elif operation == "Count":
            grand_totals[col_name] = _compute_count(all_values)

        elif operation == "Running Total":
            per_row[col_name] = _compute_running_total(all_values)
            grand_totals[col_name] = _compute_sum(all_values)

        elif operation == "% of Total":
            per_row[col_name]      = _compute_percent_of_total(all_values)
            grand_totals[col_name] = 100.0

        elif operation == "Rank":
            per_row[col_name] = _compute_rank(all_values)

        # Display → pass through, no calculation needed

    # ── GroupBy ───────────────────────────────────────────────────────────────
    groups = []
    if group_col and group_col in col_index:
        groups = _compute_groupby(
            rows      = rows,
            col_index = col_index,
            group_col = group_col,
            columns   = visible_cols,
        )
        logger.info(
            f"[COMPUTE] GroupBy {group_col} → "
            f"{len(groups)} groups"
        )

    # ── Visible rows (hidden columns removed) ─────────────────────────────────
    visible_rows = []
    for row in rows:
        visible_row = {
            col_name: _get_col_value(row, col_index, col_name)
            for col_name in column_order
        }
        visible_rows.append(visible_row)

    logger.info(
        f"[COMPUTE] done | "
        f"rows={len(visible_rows)} | "
        f"cols={len(visible_cols)} | "
        f"totals={len(grand_totals)} | "
        f"groups={len(groups)} | "
        f"per_row_cols={len(per_row)}"
    )

    return {
        "visible_rows":  visible_rows,
        "grand_totals":  grand_totals,
        "groups":        groups,
        "per_row":       per_row,
        "column_order":  column_order,
        "total_rows":    len(rows),
    }