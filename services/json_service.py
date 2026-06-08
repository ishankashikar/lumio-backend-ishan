import json
import os
import uuid
from datetime import datetime
from constants import REPORT_CONFIGS_DIR, REPORT_OUTPUTS_DIR
from core.logger import get_logger
from core.exceptions import LumioException

logger = get_logger(__name__)


def _report_path(report_id: str) -> str:
    return os.path.join(REPORT_OUTPUTS_DIR, f"{report_id}.json")


def init_report(report_type: str, session_id: str) -> dict:
    config_path = os.path.join(REPORT_CONFIGS_DIR, f"{report_type}.json")
    if not os.path.exists(config_path):
        raise LumioException(status_code=404, detail=f"No base config found for report type: {report_type}")

    with open(config_path, "r") as f:
        base = json.load(f)

    report_id = f"{report_type}_{session_id}_{uuid.uuid4().hex[:8]}"
    base["reportId"]   = report_id
    base["reportType"] = report_type
    base["createdAt"]  = datetime.utcnow().isoformat()
    base["updatedAt"]  = datetime.utcnow().isoformat()

    save_report(report_id, base)
    logger.info(f"Report initialized: {report_id}")
    return {"report_id": report_id, "json_data": base}


def save_report(report_id: str, json_data: dict) -> dict:
    os.makedirs(REPORT_OUTPUTS_DIR, exist_ok=True)
    json_data["updatedAt"] = datetime.utcnow().isoformat()

    path = _report_path(report_id)
    with open(path, "w") as f:
        json.dump(json_data, f, indent=2)

    logger.info(f"Report saved: {report_id}")
    return {"status": "ok", "report_id": report_id}


def load_report(report_id: str) -> dict:
    path = _report_path(report_id)
    if not os.path.exists(path):
        raise LumioException(status_code=404, detail=f"Report not found: {report_id}")

    with open(path, "r") as f:
        return json.load(f)


def list_reports() -> list:
    os.makedirs(REPORT_OUTPUTS_DIR, exist_ok=True)
    reports = []
    for fname in os.listdir(REPORT_OUTPUTS_DIR):
        if fname.endswith(".json"):
            path = os.path.join(REPORT_OUTPUTS_DIR, fname)
            with open(path, "r") as f:
                data = json.load(f)
            reports.append({
                "report_id":   data.get("reportId"),
                "report_type": data.get("reportType"),
                "updated_at":  data.get("updatedAt"),
            })
    return reports