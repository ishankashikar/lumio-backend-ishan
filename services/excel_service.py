"""
services/excel_service.py

Generates plain Excel (.xlsx) export from computed report data.
Uses openpyxl — already in requirements.txt.

Plain export:
→ Bank header rows at top
→ Column headers
→ Data rows
→ Grand totals row at bottom
→ No styling, no colors, no borders

User confirmed operations via column intelligence.
Renderer already computed all values.
excel_service just writes them to file.
"""

import os
from openpyxl            import Workbook
from openpyxl.styles     import Font, PatternFill, Alignment
from core.logger         import get_logger
from utils.file_utils    import ensure_export_dir
from constants           import EXPORTS_DIR

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _safe_value(value) -> str | int | float:
    """
    Converts value to Excel-safe type.
    None → empty string.
    Keeps numbers as numbers (not strings) so Excel can sum them.
    """
    if value is None:
        return ""
    try:
        # Keep as number if possible
        f = float(str(value).replace(",", "").strip())
        # Return int if whole number
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return str(value).strip()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def generate_excel(
    report_id:    str,
    config:       dict,
    computed:     dict,
) -> str:
    """
    Generates plain Excel file from computed report data.
    Called by export API endpoint.

    Args:
        report_id : used for file naming + folder
        config    : full report JSON
        computed  : output from renderer.render()

    Returns:
        Full file path of generated .xlsx file.

    Reads from confirmed JSON keys:
        config["bank"]["bankName"]
        config["bank"]["branch"]
        config["bank"]["preparedBy"]
        config["bank"]["authorizedBy"]
        config["columns"][n]["label"]
        config["columns"][n]["column"]
        config["columns"][n]["operation"]
        config["template"]
    """
    wb = Workbook()
    ws = wb.active

    bank      = config.get("bank",     {})
    columns   = computed.get("columns", [])
    rows      = computed.get("rows",    [])
    totals    = computed.get("grand_totals", {})
    groups    = computed.get("groups",  [])
    template  = config.get("template", "tabular")

    # ── Bank header ────────────────────────────────────────────────────────
    bank_name  = bank.get("bankName",  "Bank Report")
    branch     = bank.get("branch",    "")
    prepared   = bank.get("preparedBy", "")
    authorized = bank.get("authorizedBy", "")

    ws.append([bank_name])
    ws.append([f"Branch: {branch}"] if branch else [""])
    ws.append([f"Prepared By: {prepared}   Authorized By: {authorized}"])
    ws.append([])   # blank row

    # ── Column headers ─────────────────────────────────────────────────────
    headers = [
        col.get("label", col.get("column", ""))
        for col in columns
        if col.get("operation", "Display") != "Hide"
    ]
    col_names = [
        col.get("column", "")
        for col in columns
        if col.get("operation", "Display") != "Hide"
    ]

    ws.append(headers)

    # ── Data rows ──────────────────────────────────────────────────────────
    if template == "groupby" and groups:
        # GroupBy template → group header + rows + subtotals
        for group in groups:
            # Group header row
            ws.append([f"▶ {group['key']}  ({group['count']} records)"])

            # Group data rows
            for row in group.get("rows", []):
                ws.append([
                    _safe_value(row.get(col))
                    for col in col_names
                ])

            # Group subtotals row
            subtotals = group.get("subtotals", {})
            if subtotals:
                subtotal_row = []
                for col in col_names:
                    val = subtotals.get(col)
                    subtotal_row.append(
                        _safe_value(val) if val is not None else ""
                    )
                ws.append(["Subtotal"] + subtotal_row[1:])

            ws.append([])   # blank row between groups

    else:
        # Tabular template → flat rows
        for row in rows:
            ws.append([
                _safe_value(row.get(col))
                for col in col_names
            ])

    # ── Grand totals row ───────────────────────────────────────────────────
    if totals:
        totals_row = []
        for col in col_names:
            val = totals.get(col)
            totals_row.append(
                _safe_value(val) if val is not None else ""
            )
        ws.append(["Grand Total"] + totals_row[1:])

    # ── Auto column width ──────────────────────────────────────────────────
    for col_cells in ws.columns:
        max_len = 0
        for cell in col_cells:
            try:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            except Exception:
                pass
        # Cap width at 40 chars — prevents absurdly wide columns
        ws.column_dimensions[
            col_cells[0].column_letter
        ].width = min(max_len + 2, 40)

    # ── Save file ──────────────────────────────────────────────────────────
    export_dir = ensure_export_dir(report_id)
    file_path  = os.path.join(export_dir, f"{report_id}.xlsx")

    wb.save(file_path)

    logger.info(
        f"[EXCEL] Generated: {file_path} | "
        f"rows={len(rows)} | "
        f"cols={len(col_names)}"
    )

    return file_path