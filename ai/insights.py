"""
ai/insights.py

AI-driven insights engine for Lumio report builder.
Generates 3 types of insights from actual report data:

1. Data Quality   — what looks wrong, suspicious, or inconsistent
2. Customer Story — what each customer's pattern tells us
3. Action Items   — concrete things a manager/officer should do

Rules:
- Uses ONLY column_stats and column_config passed in (no invented data)
- Every insight must have: what observed + why + action
- Always generated, user chooses whether to include in export
- Goes on the last page of every report
"""

import json
import asyncio
import re

from google       import genai
from google.genai import types

from core.config  import settings
from core.logger  import get_logger
from constants    import GEMINI_MODEL

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDERS
# ═════════════════════════════════════════════════════════════════════════════

def _build_stats_context(
    column_config: list[dict],
    column_stats:  dict,
) -> str:
    """
    Converts column_stats into a readable block for Gemini.
    Only includes columns that have actual data — skips empty ones.
    """
    if not column_stats:
        return "No statistics available."

    lines = []
    for col, s in column_stats.items():

        # Find label from config
        cfg   = next(
            (c for c in column_config if c.get("column") == col), {}
        )
        label = cfg.get("label", col)
        op    = cfg.get("operation", "Display")

        # Skip hidden columns
        if op == "Hide":
            continue

        line = f"  {label} ({col}) [{op}]"

        if "sum" in s:
            line += (
                f": total={s['sum']:,.2f}"
                f", avg={s['avg']:,.2f}"
                f", min={s['min']:,.2f}"
                f", max={s['max']:,.2f}"
                f", count={s.get('count', 'N/A')}"
            )
        if "null_count" in s:
            line += f", nulls={s['null_count']}"

        if "unique_count" in s:
            line += f", unique_values={s['unique_count']}"

        if "group_counts" in s:
            top = ", ".join(
                f"{g['key']} ({g['count']} records)"
                for g in s["group_counts"][:6]
            )
            line += f"\n    Top groups: {top}"

        lines.append(line)

    return "\n".join(lines) if lines else "No usable statistics found."


def _build_column_context(column_config: list[dict]) -> str:
    """Summarises what each column represents for Gemini context."""
    visible = [
        c for c in column_config
        if c.get("operation") != "Hide"
    ]
    return ", ".join(
        f"{c.get('label', c.get('column'))} ({c.get('operation', 'Display')})"
        for c in visible[:15]
    )


# ═════════════════════════════════════════════════════════════════════════════
# GEMINI CALL (async, shared)
# ═════════════════════════════════════════════════════════════════════════════

async def _call_gemini(prompt: str, max_tokens: int = 2048) -> str:
    """Single async Gemini call. Returns raw text."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    def _sync():
        return client.models.generate_content(
            model    = GEMINI_MODEL,
            contents = prompt,
            config   = types.GenerateContentConfig(
                temperature        = 0.3,
                max_output_tokens  = max_tokens,
                response_mime_type = "application/json",
            )
        )

    try:
        response = await asyncio.to_thread(_sync)
        raw      = response.text.strip()
        raw      = re.sub(r"^```[a-z]*\n?", "", raw)
        raw      = re.sub(r"\n?```$",        "", raw)
        return raw
    except Exception as e:
        logger.error(f"[INSIGHTS] Gemini call failed: {e}")
        return ""


# ═════════════════════════════════════════════════════════════════════════════
# 1. DATA QUALITY INSIGHTS
# ═════════════════════════════════════════════════════════════════════════════

async def detect_data_quality(
    procedure_name: str,
    report_type:    str,
    total_rows:     int,
    column_config:  list[dict],
    column_stats:   dict,
) -> list[dict]:
    """
    AI looks at the actual data distribution and flags anything that
    a bank auditor or manager would stop and question.

    Not hardcoded rules — AI decides what looks wrong based on the numbers.

    Returns list of:
    {
        column, label, severity,
        what, why, action,
        value
    }
    """
    if not column_stats:
        return []

    stats_context  = _build_stats_context(column_config, column_stats)
    column_context = _build_column_context(column_config)

    prompt = f"""You are a senior bank auditor reviewing a cooperative bank report.
Look at this data and identify anything that looks wrong, suspicious, unusual, or needs explanation.

Report     : {procedure_name}
Type       : {report_type}
Total rows : {total_rows}
Columns    : {column_context}

Actual statistics from the database:
{stats_context}

Think like an auditor. Flag things such as:
- A numeric column that should always be positive but has negatives
- One group/branch dominating an unusually high share of records
- A huge gap between average and maximum (possible outlier or error)
- A column with too many NULLs when it should always have data
- A sum or total that seems too high or too low for the context
- Suspicious patterns (e.g. all transactions on same date, round numbers only)
- GL accounts with unexpected balances
- Any distribution that breaks what you would expect for this report type

For each issue explain:
WHAT  — exactly what you observed (with actual numbers)
WHY   — why this is unusual or concerning
ACTION — what a branch manager or officer should do about it

Be specific and use actual numbers from the statistics.
Maximum 6 items. Empty array if nothing genuinely notable.

Respond ONLY with valid JSON array:
[
  {{
    "column":   "<column name>",
    "label":    "<human readable label>",
    "severity": "high|medium|low",
    "what":     "<exactly what was observed with actual numbers>",
    "why":      "<why this is unusual or concerning>",
    "action":   "<concrete action for manager or officer>",
    "value":    "<the specific number or stat that triggered this>"
  }}
]"""

    raw = await _call_gemini(prompt, max_tokens=1500)
    if not raw:
        return []

    try:
        result = json.loads(raw)
        return result[:6] if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        logger.error(f"[INSIGHTS] Data quality parse failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 2. CUSTOMER STORIES
# ═════════════════════════════════════════════════════════════════════════════

async def generate_customer_stories(
    procedure_name: str,
    report_type:    str,
    total_rows:     int,
    column_config:  list[dict],
    column_stats:   dict,
) -> list[dict]:
    """
    Generates narrative insights about customer patterns visible in the data.

    Not just "prospect / dormant" labels.
    Actual stories — what is this customer doing, what does it tell us,
    what should the bank do about it.

    Uses only data that is actually present in column_stats.
    Never invents customer behaviour not supported by the numbers.

    Returns list of:
    {
        story_type, headline,
        what, why, action,
        confidence, supporting_metric
    }
    """
    if not column_stats:
        return []

    stats_context  = _build_stats_context(column_config, column_stats)
    column_context = _build_column_context(column_config)

    prompt = f"""You are a relationship manager and data analyst at a cooperative bank in India.
Look at this report data and tell meaningful stories about what the customer patterns show.

Report     : {procedure_name}
Type       : {report_type}
Total rows : {total_rows}
Columns    : {column_context}

Actual statistics from the database:
{stats_context}

Generate insights that tell a real story about customer behaviour.
Think beyond just "prospect" or "dormant" labels.

Examples of good stories:
- "Customers with growing balances but no loan product — bank is missing an opportunity"
- "A segment making regular deposits but withdrawing all before month end — possible salary account behaviour"
- "NPA accounts where outstanding is slowly reducing — these customers are trying to pay, restructuring could help"
- "Accounts with large one-time credits followed by no activity — possible fixed deposit maturity not reinvested"
- "Branch showing high disbursement but also high NPA — quality vs quantity issue"
- "Customers with multiple small transactions daily — possible small business, could benefit from current account"

Each story must have:
WHAT       — what pattern or behaviour you observed
WHY        — what it means for the bank and the customer
ACTION     — specific thing the bank should do (call, offer product, review, restructure, etc.)
CONFIDENCE — how confident you are based on available data (0.0 to 1.0)

Only include stories genuinely supported by the statistics above.
Do NOT invent patterns not visible in the data.
Maximum 8 stories.

Respond ONLY with valid JSON array:
[
  {{
    "story_type":         "opportunity|risk|behavioural|operational",
    "headline":           "<one sentence summary>",
    "what":               "<what pattern was observed with numbers>",
    "why":                "<what this means for bank and customer>",
    "action":             "<specific actionable recommendation>",
    "confidence":         0.85,
    "supporting_metric":  "<the key stat supporting this story>"
  }}
]"""

    raw = await _call_gemini(prompt, max_tokens=2048)
    if not raw:
        return []

    try:
        result = json.loads(raw)
        return result[:8] if isinstance(result, list) else []
    except json.JSONDecodeError as e:
        logger.error(f"[INSIGHTS] Customer stories parse failed: {e}")
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 3. REPORT SUMMARY (narrative for export header)
# ═════════════════════════════════════════════════════════════════════════════

async def generate_report_summary(
    procedure_name: str,
    report_type:    str,
    total_rows:     int,
    column_config:  list[dict],
    column_stats:   dict,
    template:       str,
) -> str:
    """
    Generates a 2-3 paragraph professional narrative summary.
    Printed at the top of the exported report (PDF / Excel).

    Uses actual stats — not vague generic text.
    """
    stats_context = _build_stats_context(column_config, column_stats)
    visible_cols  = [
        c.get("label", c.get("column", ""))
        for c in column_config
        if c.get("operation") != "Hide"
    ]

    prompt = f"""You are writing a professional narrative summary for a cooperative bank report.
This will be printed at the top of the exported report.

Report     : {procedure_name}
Type       : {report_type}
Template   : {template}
Total rows : {total_rows}
Columns    : {', '.join(visible_cols[:10])}

Key statistics:
{stats_context}

Write a 2-3 paragraph professional summary:
Paragraph 1 — What this report contains and its scope (dates, branches, accounts covered)
Paragraph 2 — Key numerical highlights using actual numbers from statistics above
Paragraph 3 — One brief interpretive note for the reader (optional, only if data supports it)

Rules:
- Use actual numbers. Never say "significant" or "notable" without a number.
- Formal, factual, concise — this is a banking document.
- No bullet points. No headers. Plain paragraphs only.

Respond ONLY with valid JSON:
{{"summary": "<full 2-3 paragraph text>"}}"""

    raw = await _call_gemini(prompt, max_tokens=800)
    if not raw:
        return (
            f"This report was generated from procedure '{procedure_name}' "
            f"and contains {total_rows} records."
        )

    try:
        result = json.loads(raw)
        return result.get("summary", "")
    except json.JSONDecodeError as e:
        logger.error(f"[INSIGHTS] Summary parse failed: {e}")
        return (
            f"This report was generated from procedure '{procedure_name}' "
            f"and contains {total_rows} records."
        )


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT — runs all 3 in parallel
# ═════════════════════════════════════════════════════════════════════════════

async def generate_all_insights(
    procedure_name: str,
    report_type:    str,
    total_rows:     int,
    column_config:  list[dict],
    column_stats:   dict,
    template:       str = "tabular",
) -> dict:
    """
    Runs all 3 insight generators in parallel (asyncio.gather).
    Called after report data is loaded and column operations confirmed.

    Returns:
    {
        summary          : str,
        data_quality     : list[dict],
        customer_stories : list[dict],
        has_high_severity: bool,
        total_insights   : int,
    }
    """
    if not settings.GEMINI_API_KEY:
        return {
            "summary":           "AI not configured.",
            "data_quality":      [],
            "customer_stories":  [],
            "has_high_severity": False,
            "total_insights":    0,
        }

    if not column_stats:
        logger.warning("[INSIGHTS] No column_stats provided — skipping insights")
        return {
            "summary":           "Insufficient data for insights.",
            "data_quality":      [],
            "customer_stories":  [],
            "has_high_severity": False,
            "total_insights":    0,
        }

    # Run all 3 in parallel — no waiting for each other
    summary, data_quality, customer_stories = await asyncio.gather(
        generate_report_summary(
            procedure_name = procedure_name,
            report_type    = report_type,
            total_rows     = total_rows,
            column_config  = column_config,
            column_stats   = column_stats,
            template       = template,
        ),
        detect_data_quality(
            procedure_name = procedure_name,
            report_type    = report_type,
            total_rows     = total_rows,
            column_config  = column_config,
            column_stats   = column_stats,
        ),
        generate_customer_stories(
            procedure_name = procedure_name,
            report_type    = report_type,
            total_rows     = total_rows,
            column_config  = column_config,
            column_stats   = column_stats,
        ),
    )

    has_high = any(
        item.get("severity") == "high"
        for item in data_quality
    )

    total = len(data_quality) + len(customer_stories)

    logger.info(
        f"[INSIGHTS] {procedure_name} | "
        f"quality={len(data_quality)} | "
        f"stories={len(customer_stories)} | "
        f"high_severity={has_high}"
    )

    return {
        "summary":           summary,
        "data_quality":      data_quality,
        "customer_stories":  customer_stories,
        "has_high_severity": has_high,
        "total_insights":    total,
    }