"""
api/ai.py

AI endpoints for Lumio report builder.

Endpoints:
  POST /ai/column-intelligence     → analyse columns, suggest operations
  POST /ai/confirm-columns         → user confirmed → save to Qdrant
  POST /ai/chat                    → chat with report data
  DELETE /ai/chat/{report_id}      → clear chat history
  GET /ai/chat/{report_id}/history → get chat history
  POST /ai/insights                → generate insights
"""

from fastapi     import APIRouter
from core.logger import get_logger
from models.request_models import (
    ColumnIntelligenceRequest,
    ConfirmColumnsRequest,
    ChatRequest,
    InsightsRequest,
)

router = APIRouter()
logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# COLUMN INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/column-intelligence")
async def column_intelligence(req: ColumnIntelligenceRequest):
    """
    Analyses columns from Oracle procedure result.
    Runs 3-layer pipeline: heuristics → Qdrant → Gemini.
    Called when user clicks "Analyse with AI" button.

    Returns:
        column_operations : {col: {operation, label, confidence, is_new}}
        has_new_columns   : bool (frontend shows banner if True)
    """
    from ai.column_intelligence import run_column_intelligence
    result = await run_column_intelligence(req)
    return {"status": "ok", **result}


@router.post("/confirm-columns")
async def confirm_columns(req: ConfirmColumnsRequest):
    """
    Called after user confirms or modifies AI suggestions in banner.
    Saves confirmed operations to Qdrant permanently.
    Never asked again for this bank + procedure combination.

    Returns:
        saved : list of column names saved to Qdrant
    """
    from ai.column_intelligence import confirm_column_operations
    result = await confirm_column_operations(req)
    return {"status": "ok", **result}


# ═════════════════════════════════════════════════════════════════════════════
# CHAT
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Stateful AI chatbot for the report.
    History stored per report_id.
    Always loads latest report JSON — reflects any UI changes.

    Returns:
        answer         : AI response text
        chart          : optional chart data if AI suggests one
        history_length : number of messages in session
    """
    from ai.chat                import chat as run_chat
    from services.cache_service import get_stats

    # Get column stats from Redis cache
    column_stats = await get_stats(req.bank_id, req.report_id) or {}

    result = await run_chat(
        report_id      = req.report_id,
        bank_id        = req.bank_id,
        procedure_name = req.procedure_name,
        user_message   = req.message,
        total_rows     = req.total_rows,
        column_stats   = column_stats,
    )
    return {"status": "ok", **result}


@router.delete("/chat/{report_id}")
def clear_chat(report_id: str):
    """
    Clears chat history for a report.
    Called from UI "Clear Chat" button.
    """
    from ai.chat import clear_history
    result = clear_history(report_id)
    return {"status": "ok", **result}


@router.get("/chat/{report_id}/history")
def get_chat_history(report_id: str):
    """
    Returns full chat history for a report.
    Called by frontend on page load to restore chat.
    """
    from ai.chat import get_history
    history = get_history(report_id)
    return {"status": "ok", "history": history}


# ═════════════════════════════════════════════════════════════════════════════
# INSIGHTS
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/insights")
async def insights(req: InsightsRequest):
    """
    Generates AI insights for a report:
    1. Report summary (for export header)
    2. Data quality flags (anomalies, suspicious patterns)
    3. Customer stories (behavioural patterns, opportunities)

    Always generated. User chooses whether to include in export.
    Runs all 3 in parallel — fast.

    Returns:
        summary          : 2-3 paragraph narrative
        data_quality     : list of anomaly flags
        customer_stories : list of customer pattern stories
        has_high_severity: bool
        total_insights   : int
    """
    from ai.insights            import generate_all_insights
    from services.json_service  import load_report
    from services.cache_service import get_stats

    # Load report config to get column config and metadata
    config       = load_report(req.report_id)
    column_stats = await get_stats(req.bank_id, req.report_id) or {}

    result = await generate_all_insights(
        procedure_name = config.get("procedureName", ""),
        report_type    = config.get("reportType",    ""),
        total_rows     = config.get("totalRows",     0),
        column_config  = config.get("columns",       []),
        column_stats   = column_stats,
        template       = config.get("template",      "tabular"),
    )

    return {"status": "ok", **result}