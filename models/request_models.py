from pydantic import BaseModel
from typing   import Any


class DBCreds(BaseModel):
    host:     str
    port:     str
    service:  str
    user:     str
    password: str


class ProcedureRequest(DBCreds):
    procedure_name: str


class FetchRequest(ProcedureRequest):
    in_params:    dict[str, Any] = {}
    cursor_index: int            = 0


class ReportInitRequest(BaseModel):
    report_type: str
    session_id:  str


class ReportSaveRequest(BaseModel):
    report_id: str
    json_data: dict


class ExportRequest(DBCreds):
    report_id:      str
    procedure_name: str
    in_params:      dict[str, Any] = {}
    cursor_index:   int            = 0


class ChatRequest(BaseModel):       # no DBCreds — chat never hits DB
    report_id:      str
    bank_id:        str             # needed for Redis cache key
    procedure_name: str
    message:        str
    total_rows:     int = 0


class InsightsRequest(BaseModel):   # new
    report_id: str
    bank_id:   str


class ColumnIntelligenceRequest(DBCreds):
    bank_id:        str
    procedure_name: str
    report_type:    str
    columns:        list[str]
    rows:           list[list]


class ConfirmColumnsRequest(BaseModel):
    bank_id:           str
    procedure_name:    str
    confirmed_columns: dict[str, dict]


# Kept for backwards compatibility — not actively used
class ApplyChartsRequest(BaseModel):
    report_id: str
    charts:    list[dict]


class ApplyInsightsRequest(BaseModel):
    report_id: str