from fastapi import APIRouter
from core.logger import get_logger
from models.request_models import (
    ChatRequest, ExportRequest,
    ApplyChartsRequest, ApplyInsightsRequest,
    ColumnIntelligenceRequest, ConfirmColumnsRequest
)

router = APIRouter()
logger = get_logger(__name__)


@router.post("/column-intelligence")
def column_intelligence(req: ColumnIntelligenceRequest):
    from ai.column_intelligence import run_column_intelligence
    result = run_column_intelligence(req)
    return {"status": "ok", **result}


@router.post("/confirm-columns")
def confirm_columns(req: ConfirmColumnsRequest):
    from ai.column_intelligence import confirm_column_operations
    result = confirm_column_operations(req)
    return {"status": "ok", **result}


@router.post("/chat")
def chat(req: ChatRequest):
    from ai.chat import handle_message
    result = handle_message(req)
    return {"status": "ok", **result}


@router.post("/apply-charts")
def apply_charts(req: ApplyChartsRequest):
    from ai.chat import apply_charts_to_report
    result = apply_charts_to_report(req.report_id, req.charts)
    return {"status": "ok", **result}


@router.post("/insights")
def insights(req: ExportRequest):
    from ai.insights import generate_insights
    result = generate_insights(req.report_id, req)
    return {"status": "ok", "insights": result}


@router.post("/apply-insights")
def apply_insights(req: ApplyInsightsRequest):
    from ai.insights import apply_insights_to_report
    result = apply_insights_to_report(req.report_id)
    return {"status": "ok", **result}


@router.post("/preview")
def preview(req: ExportRequest):
    from fastapi.responses import FileResponse
    from ai.renderer import render_pdf
    import os
    path = render_pdf(req.report_id, req)
    return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))