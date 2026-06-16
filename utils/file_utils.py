import os
import uuid
import shutil
import time
from datetime import datetime

from constants import REPORT_OUTPUTS_DIR, EXPORTS_DIR
from core.logger import get_logger

logger = get_logger(__name__)


def ensure_dirs():
    """
    Creates all required directories on startup.
    Called from lifespan in main.py.
    """
    for path in [REPORT_OUTPUTS_DIR, EXPORTS_DIR]:
        os.makedirs(path, exist_ok=True)
        logger.info(f"[FILES] Directory ensured: {path}")


def generate_session_id() -> str:
    """
    Generates a unique session ID.
    Format: 20240615143022_a3f9b1c2
    Used for audit trail and export file naming.
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def ensure_report_dir(report_id: str) -> str:
    """
    Creates report-specific output directory.
    Called when a report is first generated.

    Returns full path to the directory.
    """
    path = os.path.join(REPORT_OUTPUTS_DIR, report_id)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_export_dir(report_id: str) -> str:
    """
    Creates report-specific export directory.
    Called when user triggers an export.

    Returns full path to the directory.
    """
    path = os.path.join(EXPORTS_DIR, report_id)
    os.makedirs(path, exist_ok=True)
    return path


def clean_old_exports(days: int = 7):
    """
    Deletes export folders older than given days.
    Cleans entire report subfolder not just top level files.

    Our export structure:
    exports/
    └── {report_id}/
        ├── report.pdf
        ├── report.xlsx
        └── report.report
    """
    if not os.path.exists(EXPORTS_DIR):
        return

    cutoff = days * 86400
    now    = time.time()

    for entry in os.listdir(EXPORTS_DIR):
        entry_path = os.path.join(EXPORTS_DIR, entry)

        # Handle both subfolders and stray files
        if os.path.isdir(entry_path):
            age = now - os.path.getmtime(entry_path)
            if age > cutoff:
                shutil.rmtree(entry_path)
                logger.info(f"[FILES] Deleted old export folder: {entry}")

        elif os.path.isfile(entry_path):
            age = now - os.path.getmtime(entry_path)
            if age > cutoff:
                os.remove(entry_path)
                logger.info(f"[FILES] Deleted old export file: {entry}")


def clean_old_report_outputs(days: int = 30):
    """
    Deletes report output folders older than given days.
    Longer retention than exports (30 days vs 7 days)
    because chat history and configs are stored here.
    """
    if not os.path.exists(REPORT_OUTPUTS_DIR):
        return

    cutoff = days * 86400
    now    = time.time()

    for entry in os.listdir(REPORT_OUTPUTS_DIR):
        entry_path = os.path.join(REPORT_OUTPUTS_DIR, entry)

        if os.path.isdir(entry_path):
            age = now - os.path.getmtime(entry_path)
            if age > cutoff:
                shutil.rmtree(entry_path)
                logger.info(
                    f"[FILES] Deleted old report output: {entry}"
                )