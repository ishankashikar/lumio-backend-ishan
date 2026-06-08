from fastapi import APIRouter
from models.request_models import DBCreds
from services.db_service import get_connection
from core.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

@router.post("/test")
def test_connection(req: DBCreds):
    with get_connection(req) as conn:
        pass
    return {"status": "ok", "message": "Connection successful"}