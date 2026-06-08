import os
import uuid
from datetime import datetime
from constants import REPORT_OUTPUTS_DIR, ANALYSIS_CACHE_DIR, EXPORTS_DIR
from core.logger import get_logger

logger = get_logger(__name__)


def ensure_dirs():
    """Create all required directories if they don't exist."""
    for path in [REPORT_OUTPUTS_DIR, ANALYSIS_CACHE_DIR, EXPORTS_DIR]:
        os.makedirs(path, exist_ok=True)
        logger.info(f"Directory ensured: {path}")


def generate_session_id() -> str:
    """Generate a unique session ID."""
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:8]}"


def clean_old_exports(days: int = 7):
    """Delete export files older than given days."""
    import time
    now = time.time()
    cutoff = days * 86400

    if not os.path.exists(EXPORTS_DIR):
        return

    for fname in os.listdir(EXPORTS_DIR):
        fpath = os.path.join(EXPORTS_DIR, fname)
        if os.path.isfile(fpath):
            age = now - os.path.getmtime(fpath)
            if age > cutoff:
                os.remove(fpath)
                logger.info(f"Deleted old export: {fname}")