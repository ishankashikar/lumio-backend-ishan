"""
ai/column_intelligence.py

3-layer column intelligence pipeline.
Applied BEFORE JSON config is passed to the next layer.

Layer 1 → Heuristics        (free, instant — sample value patterns)
Layer 2 → Qdrant cache      (free after first use — exact + semantic)
Layer 3 → Gemini batch      (once per new procedure — all unknowns in 1 call)

─────────────────────────────────────────────────────────────────────────────
DEBUG BLOCK NOTE:
All sections marked with # ── DEBUG ── can be safely removed in production.
To disable debug output without removing code, set CI_DEBUG=false in .env
─────────────────────────────────────────────────────────────────────────────
"""

import re
import json
import uuid
import asyncio

from qdrant_client        import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)
from google       import genai
from google.genai import types

from core.config         import settings
from core.logger         import get_logger
from core.exceptions     import LumioException
from services.db_service import get_connection
from constants import (
    QDRANT_STORAGE_PATH,
    QDRANT_COLLECTION_PREFIX,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_MODEL,
    VECTOR_SIZE,
    SIMILARITY_THRESHOLD,       # recommended: 0.88
    CONFIDENCE_THRESHOLD,       # recommended: 0.80
    CI_DEBUG,                   # recommended: true in dev, false in prod
)

logger = get_logger(__name__)

# ── Qdrant client (local file-based, no Docker needed) ────────────────────────
_qdrant = QdrantClient(path=QDRANT_STORAGE_PATH)


# ═════════════════════════════════════════════════════════════════════════════
# QDRANT HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _collection_name(bank_id: str) -> str:
    return f"{QDRANT_COLLECTION_PREFIX}{bank_id.lower().replace(' ', '_')}"


async def _ensure_collection(bank_id: str) -> None:
    """Create Qdrant collection for bank if it doesn't exist yet."""
    def _sync():
        name     = _collection_name(bank_id)
        existing = [c.name for c in _qdrant.get_collections().collections]
        if name not in existing:
            _qdrant.create_collection(
                collection_name = name,
                vectors_config  = VectorParams(
                    size     = VECTOR_SIZE,
                    distance = Distance.COSINE,
                )
            )
            logger.info(f"[CI] Qdrant collection created: {name}")
    await asyncio.to_thread(_sync)


async def _embed(text: str) -> list[float]:
    """Convert text → vector using Gemini embedding model (async)."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    def _sync():
        result = client.models.embed_content(
            model    = GEMINI_EMBEDDING_MODEL,
            contents = text,
        )
        return result.embeddings[0].values
    return await asyncio.to_thread(_sync)


async def _search_exact(
    bank_id: str,
    procedure_name: str,
    column_name: str,
) -> dict | None:
    """
    Exact Qdrant lookup — same procedure + same column name.
    If found, we have seen this exact column before. Use silently.
    """
    await _ensure_collection(bank_id)
    def _sync():
        results = _qdrant.scroll(
            collection_name = _collection_name(bank_id),
            scroll_filter   = Filter(must=[
                FieldCondition(
                    key   = "procedure_name",
                    match = MatchValue(value=procedure_name)
                ),
                FieldCondition(
                    key   = "column_name",
                    match = MatchValue(value=column_name)
                ),
            ]),
            limit = 1,
        )
        points = results[0]
        return points[0].payload if points else None
    return await asyncio.to_thread(_sync)


async def _search_similar(
    bank_id: str,
    column_name: str,
    sample_values: list[str],
) -> dict | None:
    """
    Semantic Qdrant lookup — similar column from same bank, any procedure.
    Uses column name + sample values together for best semantic context.
    If found, apply silently (semantically matched from past experience).
    """
    await _ensure_collection(bank_id)

    query_text = (
        f"{column_name.replace('_', ' ').lower()} | "
        f"sample values: {', '.join(sample_values[:3])}"
    )
    embedding = await _embed(query_text)

    def _sync():
        results = _qdrant.search(
            collection_name = _collection_name(bank_id),
            query_vector    = embedding,
            limit           = 1,
            score_threshold = SIMILARITY_THRESHOLD,
        )
        return results[0].payload if results else None
    return await asyncio.to_thread(_sync)


async def _save_to_qdrant(
    bank_id:        str,
    procedure_name: str,
    column_name:    str,
    operation:      str,
    label:          str,
    meaning:        str,
) -> None:
    """
    Permanently store confirmed column intel in Qdrant.
    Embeds name + meaning together for best future semantic match accuracy.
    Called ONLY after user confirms — never on raw Gemini output.
    """
    await _ensure_collection(bank_id)

    # Name + meaning = best embedding for future similarity search
    embed_text = f"{column_name} | meaning: {meaning}"
    embedding  = await _embed(embed_text)

    # Deterministic ID — safe to upsert same column multiple times
    point_id = str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"{bank_id}_{procedure_name}_{column_name}",
    ))

    def _sync():
        _qdrant.upsert(
            collection_name = _collection_name(bank_id),
            points          = [PointStruct(
                id      = point_id,
                vector  = embedding,
                payload = {
                    "bank_id":        bank_id,
                    "procedure_name": procedure_name,
                    "column_name":    column_name,
                    "operation":      operation,
                    "label":          label,
                    "meaning":        meaning,
                }
            )]
        )
        logger.info(
            f"[CI] Saved to Qdrant: {bank_id} → "
            f"{procedure_name} → {column_name} → {operation}"
        )
    await asyncio.to_thread(_sync)


# ═════════════════════════════════════════════════════════════════════════════
# ORACLE SOURCE + SELECT BLOCK EXTRACTOR
# ═════════════════════════════════════════════════════════════════════════════

async def _fetch_procedure_source(db_creds, procedure_name: str) -> str:
    """
    Fetch full procedure source from Oracle ALL_SOURCE.
    Free — no Gemini cost. We only send the extracted SELECT block to Gemini.
    """
    def _sync():
        try:
            with get_connection(db_creds) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT text FROM ALL_SOURCE
                        WHERE  name  = :1
                        AND    owner = UPPER(:2)
                        ORDER  BY line
                        """,
                        [procedure_name.upper(), db_creds.user]
                    )
                    rows = cur.fetchall()
                    return "".join(row[0] for row in rows) if rows else ""
        except Exception as e:
            logger.warning(
                f"[CI] Could not fetch source for {procedure_name}: {e}"
            )
            return ""
    return await asyncio.to_thread(_sync)


def _extract_select_block(source: str) -> str:
    """
    Extract output column aliases from OPEN cursor FOR SELECT block.
    Full procedure = 300-700 lines (expensive to send to Gemini).
    SELECT aliases = 10-20 words (cheap, still gives operation context).

    e.g. "SUM(TransAmt) Balance, TranDate, DRAMT WITHDRAW"
      → "Balance, TranDate, WITHDRAW"
    """
    if not source:
        return ""

    pattern = r'open\s+\w+\s+for\s+select\s+(.*?)(?:\s+from\s+|\s+union\b)'
    match   = re.search(pattern, source, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""

    raw_cols = re.sub(r'\s+', ' ', match.group(1)).strip()
    aliases  = []

    for item in raw_cols.split(','):
        item  = item.strip()
        words = item.split()
        if not words:
            continue
        # Last word = alias: "SUM(TransAmt) Balance" → "Balance"
        alias = words[-1].strip().strip('()')
        if alias.upper() not in ('NULL', 'AS', 'DUAL', 'FROM', 'WHERE'):
            aliases.append(alias)

    # Cap at 25 aliases — enough context, keeps token cost minimal
    return ', '.join(aliases[:25])


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 1 — HEURISTIC PRE-CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

def _heuristic_classify(
    column_name: str,
    sample_values: list[str],
) -> tuple[str, float] | None:
    """
    Fast rule-based classification using ACTUAL sample values.
    Column name is used only as a weak secondary signal.
    Sample values are the primary truth — language of column name is irrelevant.

    Returns (operation, confidence) if confident.
    Returns None if ambiguous — goes to Gemini.
    """
    if not sample_values:
        # No data → cannot classify here → pass to Gemini
        return None

    str_vals    = [str(v).strip() for v in sample_values if str(v).strip()]
    if not str_vals:
        return None

    unique_vals = set(v.upper() for v in str_vals)

    # ── Date pattern (DD/MM/YYYY or YYYY-MM-DD) ───────────────────────────────
    date_re    = re.compile(
        r'^\d{2}[/-]\d{2}[/-]\d{4}$|^\d{4}-\d{2}-\d{2}'
    )
    date_count = sum(1 for v in str_vals if date_re.match(v))
    if date_count >= len(str_vals) * 0.8:
        return ("Display", 0.95)

    # ── DR/CR or Y/N flags → GroupBy ─────────────────────────────────────────
    if unique_vals <= {'DR', 'CR'} or unique_vals <= {'Y', 'N'}:
        return ("GroupBy", 0.95)

    # ── Very few unique string values → GroupBy (branch, type, status) ───────
    all_numeric = all(
        v.replace('.', '', 1).replace('-', '', 1).replace(',', '').isnumeric()
        for v in str_vals
    )
    if (
        len(unique_vals) <= 4
        and len(str_vals) >= 5
        and not all_numeric
    ):
        return ("GroupBy", 0.90)

    # ── Long text / sentences → Display (narration, particulars, address) ─────
    avg_length = sum(len(v) for v in str_vals) / len(str_vals)
    if avg_length > 20 and not all_numeric:
        return ("Display", 0.90)

    # ── Parse numeric values ──────────────────────────────────────────────────
    numeric = []
    for v in str_vals:
        try:
            numeric.append(float(v.replace(',', '')))
        except ValueError:
            pass

    # Mostly non-numeric text → Display
    if len(numeric) < len(str_vals) * 0.7:
        return ("Display", 0.85)

    if numeric:
        mn  = min(numeric)
        mx  = max(numeric)
        avg = sum(numeric) / len(numeric)

        # Signed mix (positive + negative) → Running Total (ledger balance)
        has_positive = any(n > 0 for n in numeric)
        has_negative = any(n < 0 for n in numeric)
        if has_positive and has_negative:
            return ("Running Total", 0.88)

        # Small decimals, avg < 30, range 0-100 → Average (interest/rate)
        if mx <= 100.0 and avg < 30.0 and any('.' in v for v in str_vals):
            return ("Average", 0.92)

        # All zero → ambiguous, let Gemini decide with SELECT block
        if mx == 0.0:
            return None

        # Large varied numbers → Sum
        if avg > 100:
            return ("Sum", 0.90)

    # Ambiguous → Gemini
    return None


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 3 — GEMINI BATCH ANALYSER
# ═════════════════════════════════════════════════════════════════════════════

async def _analyze_with_gemini(
    procedure_name:  str,
    select_block:    str,
    report_type:     str,
    unknown_columns: list[dict],
) -> dict:
    """
    Single Gemini call for ALL unknown columns (never per-column).
    Sends all 4 signals: procedure name, column name, sample values, SELECT block.
    """
    client   = genai.Client(api_key=settings.GEMINI_API_KEY)
    col_info = json.dumps(unknown_columns, indent=2)

    prompt = f"""You are an expert banking data analyst.
Analyze the output columns of this Oracle stored procedure for a cooperative bank in India.

Procedure name          : {procedure_name}
Report type             : {report_type}
Output column aliases   : {select_block}

Columns to analyze (with real sample values from the live database):
{col_info}

IMPORTANT — Column naming context:
This bank uses non-standard column names. They may be English abbreviations,
shortened regional language words, mixed names, or completely custom bank-specific codes.
Column name language is IRRELEVANT. Use sample values as the primary classification signal.
If has_data is false, use the SELECT block and procedure name as context.

Available operations:
- Sum          : Total this field   (amounts, balances, principal, outstanding, disbursed)
- Average      : Average this field (interest rates, percentages, ratios — NEVER sum these)
- Running Total: Cumulative sum     (ledger balance, statement running balance)
- Count        : Count occurrences  (account IDs, voucher numbers, transaction codes)
- GroupBy      : Group rows by this (branch, product, status, category, type codes)
- Display      : Show as-is         (dates, narrations, names, cheque nos, remarks, address)
- Hide         : Hide by default    (internal GL codes, system flags, raw IDs)

Classification rules — follow strictly:
1. Sample values are the primary truth. Override column name guesses with sample values.
2. Large varied numbers (1000+, varied)          → Sum
3. Small decimals, avg < 30, range 0-100         → Average (interest/rate field)
4. Mix of positive and negative numbers          → Running Total
5. Few repeated string values (2-5 unique)       → GroupBy
6. DD/MM/YYYY or YYYY-MM-DD pattern              → Display
7. Long free text, sentences                     → Display
8. All unique large integers                     → Count or Hide
9. DR/CR only or Y/N only                        → GroupBy
10. has_data=false + SUM() in SELECT block       → Sum
11. has_data=false + AVG() in SELECT block       → Average
12. has_data=false + no clear signal             → Display with confidence 0.60
13. All zeros                                    → Sum (column has no current data)

Assign confidence honestly:
1.00 = completely certain
0.85 = very likely
0.70 = probable
0.60 = unsure (user will see confirmation banner)

Respond ONLY with valid JSON — no markdown, no explanation:
{{
  "COLUMN_NAME": {{
    "operation":  "Sum",
    "label":      "Outstanding Loan Amount",
    "meaning":    "Total unpaid loan principal in NPA report context",
    "confidence": 0.95,
    "position":   1
  }}
}}"""

    def _sync():
        return client.models.generate_content(
            model    = GEMINI_MODEL,
            contents = prompt,
            config   = types.GenerateContentConfig(
                temperature        = 0.1,
                max_output_tokens  = 2048,
                response_mime_type = "application/json",
            )
        )

    try:
        response = await asyncio.to_thread(_sync)
        raw      = response.text.strip()
        raw      = re.sub(r"^```[a-z]*\n?", "", raw)
        raw      = re.sub(r"\n?```$",        "", raw)
        return json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"[CI] Gemini JSON parse failed: {e}")
        raise LumioException(
            status_code = 500,
            detail      = "AI column analysis failed. Please retry."
        )
    except Exception as e:
        logger.error(f"[CI] Gemini call failed: {e}")
        raise LumioException(
            status_code = 500,
            detail      = "AI service unavailable. Please retry."
        )


# ═════════════════════════════════════════════════════════════════════════════
# ── DEBUG ── (remove this entire function in production if preferred)
# Controlled by CI_DEBUG in .env — safe to leave in codebase
# ═════════════════════════════════════════════════════════════════════════════

def _debug_print_results(procedure_name: str, known: dict) -> None:
    """
    Prints a clean table of column intelligence results to the logger.
    Only runs when CI_DEBUG=true in .env
    Remove this function and its call in run_column_intelligence
    if you want zero debug code in production.
    """
    if not CI_DEBUG:
        return

    logger.info("\n" + "=" * 72)
    logger.info(f"  COLUMN INTELLIGENCE DEBUG — {procedure_name}")
    logger.info("=" * 72)
    logger.info(
        f"  {'Column':<28} {'Operation':<16} {'Conf':<6} {'Source':<10}"
    )
    logger.info("-" * 72)

    for col, data in known.items():
        if data["is_new"]:
            source = "GEMINI"
        elif data["confidence"] == 1.0:
            source = "QDRANT"
        elif data["confidence"] >= CONFIDENCE_THRESHOLD:
            source = "RULE"
        else:
            source = "RULE-LOW"

        logger.info(
            f"  {col:<28} "
            f"{data['operation']:<16} "
            f"{data['confidence']:.2f}   "
            f"{source}"
        )

    new_cols    = sum(1 for d in known.values() if d["is_new"])
    qdrant_cols = sum(1 for d in known.values() if not d["is_new"] and d["confidence"] == 1.0)
    rule_cols   = len(known) - new_cols - qdrant_cols

    logger.info("-" * 72)
    logger.info(
        f"  Total: {len(known)} | "
        f"Rules: {rule_cols} | "
        f"Qdrant: {qdrant_cols} | "
        f"Gemini: {new_cols}"
    )
    logger.info("=" * 72)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

async def run_column_intelligence(req) -> dict:
    """
    Full 3-layer pipeline. Called BEFORE JSON config is built and
    passed to the next layer.

    Expected req fields:
        bank_id        : str
        procedure_name : str
        report_type    : str
        columns        : list[str]
        rows           : list[list]   ← actual data rows from Oracle
        db_creds       : DBCredentials object
    """
    known   = {}
    unknown = []

    # Safe index map — handles duplicate column names without O(n²) lookup
    col_index = {col: i for i, col in enumerate(req.columns)}

    for col_name in req.columns:
        idx = col_index[col_name]

        # Extract real sample values from actual Oracle rows
        sample_values = [
            str(row[idx]).strip()
            for row in (req.rows or [])[:20]
            if row[idx] is not None
            and str(row[idx]).strip()
            and str(row[idx]).strip().upper() != "NONE"
        ][:10]

        has_data = len(sample_values) > 0

        # ── Layer 1: Heuristics (free, instant) ──────────────────────────────
        heuristic = _heuristic_classify(col_name, sample_values)
        if heuristic:
            operation, confidence = heuristic
            if confidence >= CONFIDENCE_THRESHOLD:
                known[col_name] = {
                    "operation":     operation,
                    "label":         col_name.replace("_", " ").title(),
                    "meaning":       f"Classified by value pattern: {operation}",
                    "position":      idx + 1,
                    "is_new":        False,        # silent — no banner
                    "confidence":    confidence,
                    "sample_values": sample_values,
                }
                continue

        # ── Layer 2a: Exact Qdrant match ──────────────────────────────────────
        cached = await _search_exact(req.bank_id, req.procedure_name, col_name)
        if cached:
            known[col_name] = {
                "operation":     cached["operation"],
                "label":         cached["label"],
                "meaning":       cached["meaning"],
                "position":      idx + 1,
                "is_new":        False,            # silent — confirmed before
                "confidence":    1.0,
                "sample_values": sample_values,
            }
            continue

        # ── Layer 2b: Semantic Qdrant match ───────────────────────────────────
        similar = await _search_similar(req.bank_id, col_name, sample_values)
        if similar:
            known[col_name] = {
                "operation":     similar["operation"],
                "label":         similar["label"],
                "meaning":       similar["meaning"],
                "position":      idx + 1,
                "is_new":        False,            # silent — semantically matched
                "confidence":    SIMILARITY_THRESHOLD,
                "sample_values": sample_values,
            }
            continue

        # ── Layer 3: Unknown → batch for Gemini ───────────────────────────────
        unknown.append({
            "name":          col_name,
            "sample_values": sample_values,
            "has_data":      has_data,             # tells Gemini if samples exist
            "index":         idx,
        })

    # ── Single Gemini call for ALL unknowns ───────────────────────────────────
    if unknown:
        source       = await _fetch_procedure_source(
            req.db_creds, req.procedure_name
        )
        select_block = _extract_select_block(source)

        gemini_results = await _analyze_with_gemini(
            procedure_name  = req.procedure_name,
            select_block    = select_block,
            report_type     = req.report_type,
            unknown_columns = unknown,
        )

        for col_data in unknown:
            col_name   = col_data["name"]
            result     = gemini_results.get(col_name, {})
            confidence = float(result.get("confidence", 0.60))

            known[col_name] = {
                "operation":     result.get("operation", "Display"),
                "label":         result.get(
                    "label",
                    col_name.replace("_", " ").title()
                ),
                "meaning":       result.get("meaning", ""),
                "position":      result.get("position", col_data["index"] + 1),
                "is_new":        True,             # frontend shows banner
                "confidence":    confidence,
                "sample_values": col_data["sample_values"],
            }

    # ── DEBUG ── remove _debug_print_results call if not needed ───────────────
    _debug_print_results(req.procedure_name, known)

    logger.info(
        f"[CI] {req.procedure_name} | "
        f"total={len(known)} | "
        f"gemini={len(unknown)} | "
        f"cached={len(known) - len(unknown)}"
    )

    return {
        "procedure_name":    req.procedure_name,
        "column_operations": known,
        "has_new_columns":   any(v["is_new"] for v in known.values()),
    }


# ═════════════════════════════════════════════════════════════════════════════
# CONFIRM + PERSIST TO QDRANT (called after user confirms banner)
# ═════════════════════════════════════════════════════════════════════════════

async def confirm_column_operations(req) -> dict:
    """
    Called after user confirms or modifies AI suggestions in the UI.
    Saves permanently to Qdrant — never shown to user again for this bank.

    Expected req fields:
        bank_id            : str
        procedure_name     : str
        confirmed_columns  : dict[col_name, {operation, label, meaning}]
    """
    await asyncio.gather(*[
        _save_to_qdrant(
            bank_id        = req.bank_id,
            procedure_name = req.procedure_name,
            column_name    = col_name,
            operation      = col_data["operation"],
            label          = col_data["label"],
            meaning        = col_data.get("meaning", ""),
        )
        for col_name, col_data in req.confirmed_columns.items()
    ])

    logger.info(
        f"[CI] Confirmed and saved: "
        f"{list(req.confirmed_columns.keys())}"
    )

    return {
        "status": "ok",
        "saved":  list(req.confirmed_columns.keys()),
    }