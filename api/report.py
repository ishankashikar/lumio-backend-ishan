from fastapi import APIRouter
from models.request_models import ReportInitRequest, ReportSaveRequest
from services.json_service import init_report, save_report, load_report, list_reports
from core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


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