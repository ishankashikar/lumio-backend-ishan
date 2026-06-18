"""
services/db_service.py

Oracle database service for Lumio report builder.
Handles connections, procedure discovery, param fetching, and execution.

Key fixes:
- NLS_DATE_FORMAT set before every procedure call (fixes ORA-01843)
- in_params normalized to uppercase (fixes case mismatch)
"""

import oracledb
from models.request_models import DBCreds, ProcedureRequest, FetchRequest
from core.logger           import get_logger
from core.exceptions       import LumioException

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ═════════════════════════════════════════════════════════════════════════════

def get_connection(creds: DBCreds):
    """
    Creates Oracle DB connection using thick mode.
    Thick mode initialized in main.py before any DB calls.
    """
    try:
        return oracledb.connect(
            user         = creds.user,
            password     = creds.password,
            host         = creds.host,
            port         = int(creds.port),
            service_name = creds.service,
        )
    except Exception as e:
        logger.error(f"[DB] Connection failed: {e}")
        raise LumioException(
            status_code = 400,
            detail      = f"Database connection failed: {str(e)}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# PROCEDURE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_procedure_args(cursor, procedure_name: str, owner: str) -> list[dict]:
    """
    Fetches procedure parameter metadata from Oracle all_arguments.
    Returns list of {name, type, direction, position}.
    """
    cursor.execute(
        """
        SELECT argument_name, data_type, in_out, position
        FROM   all_arguments
        WHERE  object_name = :1
        AND    owner       = UPPER(:2)
        ORDER  BY position
        """,
        [procedure_name.upper(), owner],
    )
    rows = cursor.fetchall()
    if not rows:
        raise LumioException(
            status_code = 404,
            detail      = f"Procedure '{procedure_name}' not found or has no parameters."
        )
    return [
        {
            "name":      row[0],
            "type":      row[1] or "REF CURSOR",
            "direction": row[2],
            "position":  row[3],
        }
        for row in rows
    ]


# ═════════════════════════════════════════════════════════════════════════════
# LIST ALL PROCEDURES
# ═════════════════════════════════════════════════════════════════════════════

def get_all_procedures(creds: DBCreds) -> list[str]:
    """
    Returns list of all procedure names for this user/schema.
    Uses all_arguments — safer than all_objects for restricted users.
    """
    with get_connection(creds) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT object_name
                FROM   all_arguments
                WHERE  owner = UPPER(:1)
                ORDER  BY object_name
                """,
                [creds.user],
            )
            rows = cursor.fetchall()
    return [row[0] for row in rows]


# ═════════════════════════════════════════════════════════════════════════════
# GET PROCEDURE PARAMS
# ═════════════════════════════════════════════════════════════════════════════

def get_procedure_params(req: ProcedureRequest) -> list[dict]:
    """
    Returns parameter list for a specific procedure.
    Used by frontend to build dynamic input form.
    """
    with get_connection(req) as conn:
        with conn.cursor() as cursor:
            return get_procedure_args(cursor, req.procedure_name, req.user)


# ═════════════════════════════════════════════════════════════════════════════
# EXECUTE PROCEDURE
# ═════════════════════════════════════════════════════════════════════════════

def execute_procedure(req: FetchRequest) -> dict:
    """
    Executes a stored procedure and returns cursor results.

    Fixes applied:
    1. NLS_DATE_FORMAT set to MM/DD/YYYY before execution
       → prevents ORA-01843 (not a valid month)
    2. in_params normalized to uppercase
       → handles both "newacno" and "NEWACNO" from frontend

    Args:
        req : FetchRequest with DB creds + procedure_name + in_params + cursor_index

    Returns:
        {
            label      : cursor label
            columns    : list of column names
            rows       : list of rows (list of lists)
            total_rows : int
        }
    """
    with get_connection(req) as conn:
        with conn.cursor() as cursor:

            # ── Fix 1: Set Oracle date format ─────────────────────────────
            # Prevents ORA-01843 when passing dates as DD/MM/YYYY strings
            cursor.execute(
                "ALTER SESSION SET NLS_DATE_FORMAT = 'MM/DD/YYYY'"
            )

            args = get_procedure_args(cursor, req.procedure_name, req.user)

            # ── Fix 2: Normalize in_params keys to uppercase ───────────────
            # Oracle param names are always uppercase
            # Frontend may send lowercase or mixed case
            normalized_params = {
                k.upper(): v
                for k, v in (req.in_params or {}).items()
            }

            call_args   = []
            cursor_vars = {}

            for arg in args:
                name      = arg["name"]
                direction = arg["direction"]
                dtype     = arg["type"]

                # REF CURSOR → output cursor (data result)
                if dtype == "REF CURSOR":
                    var = cursor.var(oracledb.CURSOR)
                    call_args.append(var)
                    cursor_vars[arg["position"]] = (
                        name or f"pos_{arg['position']}", var
                    )

                # OUT or IN/OUT → output variable (skip value)
                elif direction in ("OUT", "IN/OUT"):
                    var = cursor.var(oracledb.DB_TYPE_VARCHAR)
                    call_args.append(var)

                # IN param → get value from normalized_params
                else:
                    value = normalized_params.get(name) if name else None
                    if value is None:
                        raise LumioException(
                            status_code = 422,
                            detail      = f"Missing IN parameter: '{name}'"
                        )
                    call_args.append(value)

            # Execute procedure
            cursor.callproc(req.procedure_name.upper(), call_args)

            # Fetch all cursor results
            cursor_results = []
            for pos, (label, var) in sorted(cursor_vars.items()):
                result_cursor = var.getvalue()
                if result_cursor:
                    rows    = result_cursor.fetchall()
                    columns = [col[0] for col in result_cursor.description]
                    cursor_results.append({
                        "label":      label,
                        "columns":    columns,
                        "rows":       [list(r) for r in rows],
                        "total_rows": len(rows),
                    })

            logger.info(
                f"[DB] {req.procedure_name} executed | "
                f"cursors={len(cursor_results)}"
            )

    # Validate cursor_index
    if req.cursor_index >= len(cursor_results):
        raise LumioException(
            status_code = 422,
            detail      = (
                f"cursor_index {req.cursor_index} out of range — "
                f"procedure returned {len(cursor_results)} cursors."
            )
        )

    return cursor_results[req.cursor_index]