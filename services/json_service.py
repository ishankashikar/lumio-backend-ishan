"""
services/json_service.py

Handles all report JSON config operations:
- auto_generate_config → builds JSON from procedure params (no manual entry)
- init_report          → initializes from base config (legacy, keep for now)
- save_report          → saves JSON to disk with footer auto-populated
- load_report          → loads JSON from disk
- list_reports         → lists all saved reports
"""

import json
import os
import uuid
from datetime import datetime

from constants       import REPORT_CONFIGS_DIR, REPORT_OUTPUTS_DIR
from core.logger     import get_logger
from core.exceptions import LumioException

logger = get_logger(__name__)


# ── Default style/page/chart/watermark/grouping blocks ───────────────────────
# Used when auto-generating config from procedure
# These are Vaibhavi's confirmed defaults

_DEFAULT_STYLE = {
    "fontFamily":  "Inter",
    "fontSize":    13,
    "headerBg":    "#1e3a5f",
    "headerText":  "#ffffff",
    "altRowBg":    "#f0f4ff",
    "rowBg":       "#ffffff",
    "rowText":     "#1e293b",
    "borderColor": "#e2e8f0",
    "accentColor": "#3b6fd4",
}

_DEFAULT_PAGE = {
    "marginTop":    20,
    "marginBottom": 20,
    "marginLeft":   14,
    "marginRight":  14,
    "orientation":  "portrait",
    "size":         "A4",
}

_DEFAULT_GROUPING = {
    "groupByColumn":  "",
    "showSubtotals":  True,
    "showGrandTotal": True,
}

_DEFAULT_CHART = {
    "enabled": False,
    "type":    "bar",
    "xCol":    "",
    "yCol":    "",
    "aggFunc": "Sum",
    "title":   "",
}

_DEFAULT_WATERMARK = {
    "enabled":  False,
    "image":    "",
    "opacity":  0.62,
    "position": "center",
}


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _report_path(report_id: str) -> str:
    return os.path.join(REPORT_OUTPUTS_DIR, f"{report_id}.json")


def _build_footer() -> dict:
    """
    Builds footer block.
    generatedBy is hardcoded for now.
    TODO: Replace with actual logged-in user when auth is added.
    """
    return {
        "generatedBy": "Employee123",   # ← hardcoded, replace with auth later
        "generatedOn": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "pageNumber":  True,
    }


def _extract_header_params(procedure_params: list[dict]) -> list[str]:
    """
    Extracts IN params from procedure params list.
    Skips OUT params and REF CURSOR params — these are data outputs not headers.

    procedure_params comes from db_service.get_procedure_params()
    Each param: {name, type, direction, position}
    """
    header_params = []
    for param in procedure_params:
        direction = param.get("direction", "")
        data_type = param.get("type",      "")
        name      = param.get("name",      "")

        if not name:
            continue

        # Skip output params and cursors
        if direction in ("OUT", "IN/OUT"):
            continue
        if "CURSOR" in data_type.upper():
            continue

        header_params.append(name)

    return header_params


# ═════════════════════════════════════════════════════════════════════════════
# AUTO GENERATE CONFIG FROM PROCEDURE
# ═════════════════════════════════════════════════════════════════════════════
def auto_generate_config(
    procedure_name:   str,
    report_type:      str,
    session_id:       str,
    procedure_params: list[dict],
    in_params:        dict = None,
    db_creds                = None,   # ← added for bank master fetch
    branch_code:      int  = 1,       # ← hardcoded for now, TODO: from auth
) -> dict:
    """
    Auto-generates a report JSON config from procedure metadata.
    No manual JSON writing needed.

    Args:
        procedure_name   : Oracle procedure name
        report_type      : NPA / SOA / TrialBalance etc.
        session_id       : current session ID
        procedure_params : from db_service.get_procedure_params()
        in_params        : values user entered for IN params
        db_creds         : DBCreds for bank master fetch
        branch_code      : branch to fetch (default 1 = HO)
                           TODO: pass actual branch when auth is added

    Returns:
        Complete report JSON config ready for use.
        Saved to disk automatically.
    """
    from services.bank_service import fetch_bank_master, _empty_bank

    report_id     = f"{report_type}_{session_id}_{uuid.uuid4().hex[:8]}"
    header_params = _extract_header_params(procedure_params)

    # ── Fetch bank master from Oracle ─────────────────────────────────────
    # If fetch fails → use empty bank dict (report still works)
    if db_creds:
        try:
            bank_info = fetch_bank_master(db_creds, branch_code)
        except Exception as e:
            logger.warning(
                f"[JSON] Bank master fetch failed — "
                f"using empty bank info: {e}"
            )
            bank_info = _empty_bank()
    else:
        logger.warning("[JSON] No db_creds passed — using empty bank info")
        bank_info = _empty_bank()

    config = {
        # ── Identity ──────────────────────────────────────────────────────
        "version":       "1.0",
        "reportId":      report_id,
        "reportType":    report_type,
        "procedureName": procedure_name,
        "createdAt":     datetime.utcnow().isoformat(),
        "updatedAt":     datetime.utcnow().isoformat(),
        "totalRows":     0,   # updated after data fetch

        # ── Template ──────────────────────────────────────────────────────
        "template": "tabular",   # default, user can change in UI

        # ── Input params (what user entered) ──────────────────────────────
        "inParams":    in_params or {},
        "cursorIndex": 0,

        # ── Bank info (fetched from Oracle) ───────────────────────────────
        "bank": bank_info,

        # ── Report title ──────────────────────────────────────────────────
        # Defaults to procedure name — user can change in UI
        "reportTitle": procedure_name.replace("_", " ").title(),

        # ── Header params ─────────────────────────────────────────────────
        # IN params extracted from procedure — shown in report header
        # e.g. ["fromdate", "todate", "branch_code"]
        "headerParams": header_params,

        # ── Footer ────────────────────────────────────────────────────────
        "footer": _build_footer(),

        # ── Columns ───────────────────────────────────────────────────────
        # Empty until column intelligence runs
        "columns": [],

        # ── Watermark ─────────────────────────────────────────────────────
        # Image comes from bank_info (BANK_INFO_MAST.WATERMARK BLOB)
        # Override default watermark with DB watermark if available
        "watermark": {
            "enabled":  bool(bank_info.get("watermark")),
            "image":    bank_info.get("watermark", ""),
            "opacity":  0.62,
            "position": "center",
        },

        # ── Defaults from Vaibhavi's confirmed JSON ────────────────────────
        "style":    _DEFAULT_STYLE,
        "page":     _DEFAULT_PAGE,
        "grouping": _DEFAULT_GROUPING,
        "chart":    _DEFAULT_CHART,
    }

    # Save to disk immediately
    save_report(report_id, config)

    logger.info(
        f"[JSON] Auto-generated config: {report_id} | "
        f"procedure={procedure_name} | "
        f"header_params={header_params} | "
        f"bank={bank_info.get('bankName', 'unknown')}"
    )

    return {"report_id": report_id, "json_data": config}


# ═════════════════════════════════════════════════════════════════════════════
# INIT REPORT (legacy — kept for backwards compatibility)
# ═════════════════════════════════════════════════════════════════════════════

def init_report(report_type: str, session_id: str) -> dict:
    """
    Initializes report from base config file.
    Legacy function — use auto_generate_config() for new reports.
    Kept for backwards compatibility.
    """
    config_path = os.path.join(REPORT_CONFIGS_DIR, f"{report_type}.json")
    if not os.path.exists(config_path):
        raise LumioException(
            status_code = 404,
            detail      = f"No base config found for report type: {report_type}"
        )

    with open(config_path, "r") as f:
        base = json.load(f)

    report_id          = f"{report_type}_{session_id}_{uuid.uuid4().hex[:8]}"
    base["reportId"]   = report_id
    base["reportType"] = report_type
    base["createdAt"]  = datetime.utcnow().isoformat()
    base["updatedAt"]  = datetime.utcnow().isoformat()
    base["footer"]     = _build_footer()

    save_report(report_id, base)
    logger.info(f"[JSON] Report initialized: {report_id}")
    return {"report_id": report_id, "json_data": base}


# ═════════════════════════════════════════════════════════════════════════════
# SAVE
# ═════════════════════════════════════════════════════════════════════════════

def save_report(report_id: str, json_data: dict) -> dict:
    """
    Saves report JSON to disk.
    Auto-updates updatedAt and footer on every save.
    Called by:
    → auto_generate_config (initial creation)
    → /report/save endpoint (every UI change)
    → any service that modifies config
    """
    os.makedirs(REPORT_OUTPUTS_DIR, exist_ok=True)

    # Auto-update on every save
    json_data["updatedAt"] = datetime.utcnow().isoformat()
    json_data["footer"]    = _build_footer()

    path = _report_path(report_id)
    with open(path, "w") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    logger.info(f"[JSON] Report saved: {report_id}")
    return {"status": "ok", "report_id": report_id}


# ═════════════════════════════════════════════════════════════════════════════
# LOAD
# ═════════════════════════════════════════════════════════════════════════════

def load_report(report_id: str) -> dict:
    """
    Loads report JSON from disk.
    Always returns latest saved state.
    """
    path = _report_path(report_id)
    if not os.path.exists(path):
        raise LumioException(
            status_code = 404,
            detail      = f"Report not found: {report_id}"
        )

    with open(path, "r") as f:
        return json.load(f)


# ═════════════════════════════════════════════════════════════════════════════
# LIST
# ═════════════════════════════════════════════════════════════════════════════

def list_reports() -> list:
    """Lists all saved reports with basic metadata."""
    os.makedirs(REPORT_OUTPUTS_DIR, exist_ok=True)
    reports = []

    for fname in os.listdir(REPORT_OUTPUTS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(REPORT_OUTPUTS_DIR, fname)
        try:
            with open(path, "r") as f:
                data = json.load(f)
            reports.append({
                "report_id":      data.get("reportId"),
                "report_type":    data.get("reportType"),
                "procedure_name": data.get("procedureName"),
                "updated_at":     data.get("updatedAt"),
                "template":       data.get("template", "tabular"),
            })
        except Exception as e:
            logger.warning(f"[JSON] Could not read {fname}: {e}")
            continue

    return reports


# ═════════════════════════════════════════════════════════════════════════════
# UPDATE TOTAL ROWS (called after data fetch)
# ═════════════════════════════════════════════════════════════════════════════

def update_total_rows(report_id: str, total_rows: int) -> None:
    """
    Updates totalRows in JSON after procedure fetch.
    Needed by insights endpoint.
    Called from API after execute_procedure returns rows.
    """
    config               = load_report(report_id)
    config["totalRows"]  = total_rows
    save_report(report_id, config)
    logger.info(f"[JSON] totalRows updated: {report_id} → {total_rows}")