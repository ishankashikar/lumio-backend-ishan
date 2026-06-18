"""
api/export.py

Export endpoints for Lumio report builder.

PDF    → frontend handles via jsPDF (no backend endpoint)
Excel  → backend generates via excel_service
.report → backend generates via bundle_service
"""

import os

from fastapi           import APIRouter
from fastapi.responses import FileResponse

from models.request_models  import ExportRequest, FetchRequest
from services.json_service   import load_report
from services.db_service     import execute_procedure
from services.bundle_service import create_bundle
from services.excel_service  import generate_excel
from services.cache_service  import get_stats
from ai.renderer             import render
from core.logger             import get_logger
from core.exceptions         import LumioException

router = APIRouter()
logger = get_logger(__name__)


@router.post("/excel")
async def export_excel(req: ExportRequest):
    """
    Generates Excel export for a report.

    Steps:
    1. Load report config from JSON
    2. Fetch raw rows from Oracle using procedure
    3. Get cached column_stats from Redis
    4. Run renderer → computed data
    5. Generate Excel file
    6. Return file for download
    """
    # ── Step 1: Load report config ─────────────────────────────────────────
    config = load_report(req.report_id)

    # ── Step 2: Fetch raw rows from Oracle ────────────────────────────────
    try:
        fetch_req = FetchRequest(
            host           = req.host,
            port           = req.port,
            service        = req.service,
            user           = req.user,
            password       = req.password,
            procedure_name = req.procedure_name,
            in_params      = req.in_params,
            cursor_index   = req.cursor_index,
        )
        result   = execute_procedure(fetch_req)
        raw_rows = result["rows"]

    except LumioException:
        raise
    except Exception as e:
        logger.error(f"[EXPORT] DB fetch failed: {e}")
        raise LumioException(
            status_code = 500,
            detail      = "Could not fetch report data from database."
        )

    # ── Step 3: Get cached column stats from Redis ─────────────────────────
    bank_id      = config.get("bank", {}).get("bankName", "default")
    column_stats = await get_stats(bank_id, req.report_id) or {}

    # ── Step 4: Run renderer → computed data ──────────────────────────────
    computed = render(
        config       = config,
        rows         = raw_rows,
        column_stats = column_stats,
        page         = 1,
    )

    # ── Step 5: Generate Excel file ───────────────────────────────────────
    file_path = generate_excel(
        report_id = req.report_id,
        config    = config,
        computed  = computed,
    )

    if not os.path.exists(file_path):
        raise LumioException(
            status_code = 500,
            detail      = "Excel file generation failed."
        )

    # ── Step 6: Return file ───────────────────────────────────────────────
    logger.info(f"[EXPORT] Excel ready: {file_path}")

    return FileResponse(
        path       = file_path,
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename   = f"{req.report_id}.xlsx",
    )


@router.post("/report")
async def export_report_bundle(req: ExportRequest):
    """
    Generates .report bundle export.
    Contains config JSON only — PDF handled by frontend.
    """
    # Verify report exists
    load_report(req.report_id)

    try:
        path = create_bundle(req.report_id)
    except LumioException:
        raise
    except Exception as e:
        logger.error(f"[EXPORT] Bundle creation failed: {e}")
        raise LumioException(
            status_code = 500,
            detail      = "Could not create .report bundle."
        )

    logger.info(f"[EXPORT] Bundle ready: {path}")

    return FileResponse(
        path       = path,
        media_type = "application/octet-stream",
        filename   = os.path.basename(path),
    )


# ── PDF endpoint removed ───────────────────────────────────────────────────
# Frontend handles PDF entirely via jsPDF + jspdf-autotable
# No backend PDF generation needed