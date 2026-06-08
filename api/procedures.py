from fastapi import APIRouter
from models.request_models import DBCreds, ProcedureRequest, FetchRequest
from services.db_service import get_all_procedures, get_procedure_params, execute_procedure
from core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.post("/list")
def list_procedures(req: DBCreds):
    procedures = get_all_procedures(req)
    return {"status": "ok", "procedures": procedures}


@router.post("/params")
def procedure_params(req: ProcedureRequest):
    params = get_procedure_params(req)
    return {"status": "ok", "parameters": params}


@router.post("/fetch")
def fetch_records(req: FetchRequest):
    result = execute_procedure(req)
    return {"status": "ok", **result}