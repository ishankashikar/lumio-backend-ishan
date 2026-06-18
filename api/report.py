"""
api/report.py
"""

from fastapi     import APIRouter
from core.logger import get_logger

from models.request_models import (
    ReportInitRequest,
    ReportSaveRequest,
    ProcedureRequest,        # ← changed from FetchRequest
)
from services.json_service import (
    auto_generate_config,
    init_report,
    save_report,
    load_report,
    list_reports,
)
from services.db_service import get_procedure_params

router = APIRouter()
logger = get_logger(__name__)


@router.post("/generate", operation_id="generate_report")  # ← fixes duplicate warning
def generate(req: ProcedureRequest):                        # ← changed from FetchRequest
    """
    Main entry point for new reports.
    Auto-generates report JSON config from procedure metadata.
    """
    params = get_procedure_params(req)

    result = auto_generate_config(
        procedure_name   = req.procedure_name,
        report_type      = req.procedure_name,
        session_id       = "default",
        procedure_params = params,
        db_creds         = req,
    )

    logger.info(f"[REPORT] Generated: {result['report_id']}")
    return {"status": "ok", **result}


@router.post("/init")
def init(req: ReportInitRequest):
    result = init_report(req.report_type, req.session_id)
    return {"status": "ok", **result}


@router.post("/save")
def save(req: ReportSaveRequest):
    result = save_report(req.report_id, req.json_data)
    return {"status": "ok", **result}


@router.get("/load/{report_id}")
def load(report_id: str):
    json_data = load_report(report_id)
    return {"status": "ok", "json_data": json_data}


@router.get("/list")
def list_all():
    reports = list_reports()
    return {"status": "ok", "reports": reports}