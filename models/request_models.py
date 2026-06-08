from pydantic import BaseModel
from typing import Any

class DBCreds(BaseModel):
    host: str
    port: str
    service: str
    user: str
    password: str

class ProcedureRequest(DBCreds):
    procedure_name: str

class FetchRequest(ProcedureRequest):
    in_params: dict[str, Any] = {}
    cursor_index: int = 0

class ReportInitRequest(BaseModel):
    report_type: str
    session_id: str

class ReportSaveRequest(BaseModel):
    report_id: str
    json_data: dict

class ExportRequest(DBCreds):
    report_id: str

class ChatRequest(DBCreds):
    report_id: str
    session_id: str
    message: str

class ApplyChartsRequest(BaseModel):
    report_id: str
    charts: list[dict]

class ApplyInsightsRequest(BaseModel):
    report_id: str