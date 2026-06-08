import oracledb
from models.request_models import DBCreds, ProcedureRequest, FetchRequest
from core.logger import get_logger
from core.exceptions import LumioException

logger = get_logger(__name__)

def get_connection(creds: DBCreds):
    try:
        return oracledb.connect(
            user=creds.user,
            password=creds.password,
            host=creds.host,
            port=int(creds.port),
            service_name=creds.service,
        )
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        raise LumioException(status_code=400, detail=f"Database connection failed: {str(e)}")


def get_procedure_args(cursor, procedure_name: str, owner: str):
    cursor.execute(
        """
        SELECT argument_name, data_type, in_out, position
        FROM   all_arguments
        WHERE  object_name = :1
        AND    owner = UPPER(:2)
        ORDER  BY position
        """,
        [procedure_name.upper(), owner],
    )
    rows = cursor.fetchall()
    if not rows:
        raise LumioException(
            status_code=404,
            detail=f"Procedure '{procedure_name}' not found or has no parameters."
        )
    return [
        {
            "name": row[0],
            "type": row[1] or "REF CURSOR",
            "direction": row[2],
            "position": row[3],
        }
        for row in rows
    ]


def get_all_procedures(creds: DBCreds):
    with get_connection(creds) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT object_name
                FROM all_arguments
                WHERE object_type = 'PROCEDURE'
                AND owner = UPPER(:1)
                ORDER BY object_name
                """,
                [creds.user],
            )
            rows = cursor.fetchall()
    return [row[0] for row in rows]


def get_procedure_params(req: ProcedureRequest):
    with get_connection(req) as conn:
        with conn.cursor() as cursor:
            return get_procedure_args(cursor, req.procedure_name, req.user)


def execute_procedure(req: FetchRequest):
    with get_connection(req) as conn:
        with conn.cursor() as cursor:
            args = get_procedure_args(cursor, req.procedure_name, req.user)

            call_args = []
            cursor_vars = {}

            for arg in args:
                name      = arg["name"]
                direction = arg["direction"]
                dtype     = arg["type"]

                if dtype == "REF CURSOR":
                    var = cursor.var(oracledb.CURSOR)
                    call_args.append(var)
                    cursor_vars[arg["position"]] = (name or f"pos_{arg['position']}", var)

                elif direction in ("OUT", "IN/OUT"):
                    var = cursor.var(oracledb.DB_TYPE_VARCHAR)
                    call_args.append(var)

                else:
                    value = req.in_params.get(name) if name else None
                    if value is None:
                        raise LumioException(
                            status_code=422,
                            detail=f"Missing IN parameter: '{name}'"
                        )
                    call_args.append(value)

            cursor.callproc(req.procedure_name.upper(), call_args)

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

    if req.cursor_index >= len(cursor_results):
        raise LumioException(
            status_code=422,
            detail=f"cursor_index {req.cursor_index} out of range — procedure returned {len(cursor_results)} cursors."
        )

    return cursor_results[req.cursor_index]