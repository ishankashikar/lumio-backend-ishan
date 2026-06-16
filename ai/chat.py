"""
ai/chat.py

Stateful AI chatbot for Lumio report builder.

Key behaviours:
- History stored as JSON per report_id (same folder as report JSON)
- Report config reloaded FRESH on every message — so if user edits
  columns / formatting in the UI, the very next chat message reflects it
- Async Gemini call (non-blocking, FastAPI stays responsive)
- Chart output validated before returning to frontend
- Rolling history window — never grows unbounded
"""

import json
import asyncio
import re
from pathlib import Path
from datetime import datetime

from google import genai
from google.genai import types

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_MODEL      = "gemini-2.5-flash"
MAX_HISTORY       = 20          # rolling window: last 20 messages (10 pairs)
REPORT_OUTPUT_DIR = Path("report_outputs")
REPORT_CONFIG_DIR = Path("report_configs")

VALID_CHART_TYPES = {"bar", "pie", "line", "donut"}
MAX_CHART_ITEMS   = 12          # cap labels/data arrays at 12


# ── Chat History ──────────────────────────────────────────────────────────────

def _history_path(report_id: str) -> Path:
    return REPORT_OUTPUT_DIR / report_id / "chat_history.json"


def _load_history(report_id: str) -> list[dict]:
    path = _history_path(report_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[CHAT] Could not load history for {report_id}: {e}")
        return []


def _save_history(report_id: str, history: list[dict]) -> None:
    path = _history_path(report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(history, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"[CHAT] Could not save history for {report_id}: {e}")


def _trim_history(history: list[dict]) -> list[dict]:
    """Keep last MAX_HISTORY messages so context window stays reasonable."""
    return history[-MAX_HISTORY:]


# ── Report Config (always fresh) ──────────────────────────────────────────────

def _load_report_config(report_id: str) -> dict:
    """
    Loads the LATEST saved report JSON from disk.
    Called on every chat message so any UI changes are immediately visible.

    Search order:
    1. report_outputs/{report_id}/{report_id}.json  ← live edited config
    2. report_configs/*.json where reportId matches ← base config fallback
    """
    # 1. Live config (auto-saved by UI)
    live = REPORT_OUTPUT_DIR / report_id / f"{report_id}.json"
    if live.exists():
        try:
            return json.loads(live.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[CHAT] Could not read live config {live}: {e}")

    # 2. Base config fallback
    for f in REPORT_CONFIG_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("reportId") == report_id:
                return data
        except Exception:
            continue

    logger.warning(f"[CHAT] No config found for report_id={report_id}")
    return {}


def _config_to_context(config: dict) -> str:
    """
    Converts report JSON → readable text block for Gemini.
    Only includes what is useful for answering questions.
    """
    if not config:
        return "No report configuration loaded."

    lines = []

    if config.get("reportId"):
        lines.append(f"Report ID      : {config['reportId']}")
    if config.get("templateId"):
        lines.append(f"Template       : {config['templateId']}")
    if config.get("layoutType"):
        lines.append(f"Layout         : {config['layoutType']}")
    if config.get("dataSourceId"):
        lines.append(f"Data source    : {config['dataSourceId']}")

    col_ops = config.get("columnOperations", {})
    if col_ops:
        lines.append("\nCurrent column configuration:")
        for col, cfg in col_ops.items():
            label  = cfg.get("label", col)
            op     = cfg.get("operation", "Display")
            hidden = cfg.get("hidden", False)
            status = " [hidden]" if hidden else ""
            lines.append(f"  {label} ({col}) → {op}{status}")

    grouping = config.get("groupingRules", {})
    if grouping:
        lines.append(f"\nGrouping rules : {json.dumps(grouping)}")

    formatting = config.get("formattingRules", {})
    if formatting:
        lines.append(f"Formatting     : {json.dumps(formatting)}")

    return "\n".join(lines)


# ── Statistics Context ────────────────────────────────────────────────────────

def _stats_to_context(column_stats: dict) -> str:
    """Converts pre-computed stats → readable text for Gemini."""
    if not column_stats:
        return ""

    lines = ["\nPre-computed column statistics (use these for exact answers):"]
    for col, s in column_stats.items():
        label = s.get("label", col)
        line  = f"  {label} ({col})"

        if "sum" in s:
            line += (
                f": sum={s['sum']:,.2f}"
                f", avg={s['avg']:,.2f}"
                f", min={s['min']:,.2f}"
                f", max={s['max']:,.2f}"
            )
        if "count" in s:
            line += f", count={s['count']}"
        if "unique_count" in s:
            line += f", unique={s['unique_count']}"
        if "group_counts" in s:
            top   = ", ".join(
                f"{g['key']}={g['count']}"
                for g in s["group_counts"][:5]
            )
            line += f", top groups: [{top}]"

        lines.append(line)

    return "\n".join(lines)


# ── Chart Validator ───────────────────────────────────────────────────────────

def _validate_chart(chart: dict | None) -> dict | None:
    """
    Validates and sanitises chart output from Gemini.
    Prevents frontend crashes from mismatched arrays or unknown types.
    """
    if not chart or not isinstance(chart, dict):
        return None

    chart_type = chart.get("type", "")
    labels     = chart.get("labels") or []
    data       = chart.get("data") or []
    title      = chart.get("title", "")

    if chart_type not in VALID_CHART_TYPES:
        logger.warning(f"[CHAT] Unknown chart type '{chart_type}' — dropping chart")
        return None
    if not labels or not data:
        return None

    # Fix mismatched array lengths — never crash frontend
    min_len          = min(len(labels), len(data))
    chart["labels"]  = labels[:min(min_len, MAX_CHART_ITEMS)]
    chart["data"]    = data[:min(min_len, MAX_CHART_ITEMS)]
    chart["type"]    = chart_type
    chart["title"]   = title

    return chart


# ── System Prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(
    procedure_name:  str,
    total_rows:      int,
    config_context:  str,
    stats_context:   str,
) -> str:
    return f"""You are an intelligent data analyst assistant for Lumio — a cooperative banking report builder.
Your job is to help the user understand their report data clearly and accurately.

Procedure : {procedure_name}
Total rows : {total_rows}

{config_context}
{stats_context}

STRICT RULES:
1. Always use exact numbers from the pre-computed statistics. Never guess or estimate.
2. If the user asks to change columns, operations, or formatting — acknowledge politely and tell them to use the report designer panel. You cannot change the config yourself.
3. The report config shown above is the LATEST saved state. If the user recently made changes in the UI, those are already reflected here.
4. Keep answers to 2–4 sentences. Bold key numbers using **value** syntax.
5. Only include a chart if it genuinely helps visualise the answer. Never force a chart.
6. Be professional and concise — this is an internal banking tool used by clerks and officers.
7. If asked something outside the data scope, say so clearly.

Respond ONLY with valid JSON — no markdown, no explanation outside the JSON:
{{
  "answer": "<2-4 sentence answer with **bold** numbers>",
  "chart": null
}}

OR if a chart genuinely helps:
{{
  "answer": "<answer>",
  "chart": {{
    "type": "bar|pie|line|donut",
    "title": "<chart title>",
    "labels": ["label1", "label2", ...],
    "data": [number1, number2, ...]
  }}
}}"""


# ── Gemini Call (async) ───────────────────────────────────────────────────────

async def _call_gemini(system_prompt: str, history: list[dict]) -> dict:
    """
    Sends full conversation history to Gemini.
    Uses asyncio.to_thread so FastAPI event loop is never blocked.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    # Convert history → Gemini Content objects
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(
                role  = role,
                parts = [types.Part(text=msg["content"])]
            )
        )

    def _sync_call():
        return client.models.generate_content(
            model    = GEMINI_MODEL,
            contents = contents,
            config   = types.GenerateContentConfig(
                system_instruction = system_prompt,
                temperature        = 0.3,
                max_output_tokens  = 1024,
                response_mime_type = "application/json",
            )
        )

    try:
        response = await asyncio.to_thread(_sync_call)

        raw = response.text.strip()
        # Strip markdown fences just in case (response_mime_type should prevent them)
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$",        "", raw)

        result = json.loads(raw)
        return {
            "answer": result.get("answer", "Sorry, I could not process that."),
            "chart":  _validate_chart(result.get("chart")),
        }

    except json.JSONDecodeError as e:
        logger.error(f"[CHAT] JSON parse failed: {e}")
        return {
            "answer": "I had trouble reading the AI response. Please try again.",
            "chart":  None,
        }
    except Exception as e:
        logger.error(f"[CHAT] Gemini call error: {e}")
        return {
            "answer": "Something went wrong. Please try again.",
            "chart":  None,
        }


# ── Public API ────────────────────────────────────────────────────────────────

async def chat(
    report_id:      str,
    bank_id:        str,
    procedure_name: str,
    user_message:   str,
    total_rows:     int  = 0,
    column_stats:   dict = None,
) -> dict:
    """
    Main chat entry point — called by the API router.

    Args:
        report_id      : Unique report identifier (also the chat session ID)
        bank_id        : Bank identifier (for isolation, future Qdrant memory)
        procedure_name : Oracle procedure that generated the report
        user_message   : What the user typed
        total_rows     : Total records in the report
        column_stats   : Pre-computed stats from the data service

    Returns:
        {
            answer         : str,
            chart          : dict | None,
            history_length : int
        }
    """
    if not settings.GEMINI_API_KEY:
        return {
            "answer":         "AI is not configured. Please set GEMINI_API_KEY in .env",
            "chart":          None,
            "history_length": 0,
        }

    if not user_message or not user_message.strip():
        return {
            "answer":         "Please type a question.",
            "chart":          None,
            "history_length": 0,
        }

    # ── 1. Load latest report config (FRESH — reflects any UI changes) ────────
    config         = _load_report_config(report_id)
    config_context = _config_to_context(config)
    stats_context  = _stats_to_context(column_stats or {})

    # ── 2. Load existing chat history ─────────────────────────────────────────
    history = _load_history(report_id)

    # ── 3. Build system prompt with current config ────────────────────────────
    system_prompt = _build_system_prompt(
        procedure_name = procedure_name,
        total_rows     = total_rows,
        config_context = config_context,
        stats_context  = stats_context,
    )

    # ── 4. Append user message to history ────────────────────────────────────
    history.append({
        "role":      "user",
        "content":   user_message.strip(),
        "timestamp": datetime.now().isoformat(),
    })

    # ── 5. Call Gemini with full conversation history ─────────────────────────
    result = await _call_gemini(system_prompt, history)

    # ── 6. Append assistant reply to history ──────────────────────────────────
    history.append({
        "role":      "assistant",
        "content":   result["answer"],
        "timestamp": datetime.now().isoformat(),
    })

    # ── 7. Trim + persist history ─────────────────────────────────────────────
    history = _trim_history(history)
    _save_history(report_id, history)

    logger.info(
        f"[CHAT] {bank_id}/{report_id} | "
        f"Q: {user_message[:60]!r} | "
        f"A: {result['answer'][:60]!r} | "
        f"chart={result['chart'] is not None} | "
        f"history={len(history)}"
    )

    return {
        "answer":         result["answer"],
        "chart":          result["chart"],
        "history_length": len(history),
    }


def clear_history(report_id: str) -> dict:
    """Wipes the chat history for a report. Called from UI 'Clear Chat'."""
    path = _history_path(report_id)
    if path.exists():
        path.unlink()
        logger.info(f"[CHAT] History cleared: {report_id}")
    return {"status": "ok", "report_id": report_id}


def get_history(report_id: str) -> list[dict]:
    """Returns full chat history for a report. Used by frontend to restore chat on page load."""
    return _load_history(report_id)