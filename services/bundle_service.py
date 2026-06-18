"""
services/bundle_service.py

Generates .report bundle export.
Bundle = config JSON + report data zipped together.

PDF is handled by frontend (jsPDF) — not included in bundle.
Bundle contains:
  - {report_id}.json  ← full report config
"""

import os
import zipfile

from constants       import EXPORTS_DIR, REPORT_OUTPUTS_DIR
from core.exceptions import LumioException
from core.logger     import get_logger

logger = get_logger(__name__)


def create_bundle(report_id: str) -> str:
    """
    Creates .report bundle for a report.
    Bundle includes config JSON only.
    PDF is excluded — frontend handles PDF via jsPDF.

    Args:
        report_id : report identifier

    Returns:
        Full path to generated .report bundle file.
    """
    json_path   = os.path.join(REPORT_OUTPUTS_DIR, f"{report_id}.json")
    bundle_dir  = os.path.join(EXPORTS_DIR, report_id)
    bundle_path = os.path.join(bundle_dir, f"{report_id}.report")

    # Config JSON must exist
    if not os.path.exists(json_path):
        raise LumioException(
            status_code = 404,
            detail      = f"Report config not found: {report_id}"
        )

    # Create export dir if needed
    os.makedirs(bundle_dir, exist_ok=True)

    # Bundle it
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=f"{report_id}.json")

    logger.info(f"[BUNDLE] Created: {bundle_path}")
    return bundle_path