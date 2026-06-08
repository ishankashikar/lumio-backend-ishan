import os
import zipfile
from constants import EXPORTS_DIR
from core.exceptions import LumioException
from core.logger import get_logger

logger = get_logger(__name__)


def create_bundle(report_id: str) -> str:
    pdf_path    = os.path.join(EXPORTS_DIR, f"{report_id}.pdf")
    json_path   = os.path.join("report_outputs", f"{report_id}.json")
    bundle_path = os.path.join(EXPORTS_DIR, f"{report_id}.report")

    if not os.path.exists(pdf_path):
        raise LumioException(status_code=404, detail=f"PDF not found for report: {report_id}. Generate PDF first.")
    if not os.path.exists(json_path):
        raise LumioException(status_code=404, detail=f"Report JSON not found: {report_id}")

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(pdf_path,  arcname=f"{report_id}.pdf")
        zf.write(json_path, arcname=f"{report_id}.json")

    logger.info(f"Bundle created: {bundle_path}")
    return bundle_path