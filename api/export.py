from fastapi import APIRouter
from fastapi.responses import FileResponse
from models.request_models import ExportRequest
from services.json_service import load_report
from services.bundle_service import create_bundle
from core.logger import get_logger
from core.exceptions import LumioException
import os

router = APIRouter()
logger = get_logger(__name__)


@router.post("/pdf")
def export_pdf(req: ExportRequest):
    from ai.renderer import render_pdf
    path = render_pdf(req.report_id, req)
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))


@router.post("/excel")
def export_excel(req: ExportRequest):
    from services.excel_service import generate_excel
    path = generate_excel(req.report_id, req)
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=os.path.basename(path))


@router.post("/report")
def export_report_bundle(req: ExportRequest):
    from ai.renderer import render_pdf
    render_pdf(req.report_id, req)
    path = create_bundle(req.report_id)
    return FileResponse(path, media_type="application/octet-stream", filename=os.path.basename(path))