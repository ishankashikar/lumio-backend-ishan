"""
services/bank_service.py

Fetches complete bank and branch information from Oracle.
Called once at report generation — stored in JSON config.
Never called again unless report is regenerated.

Tables used:
    BANK_INFO_MAST       → bank name, logo, watermark, signatures
    ADDRESS_DETAILS_OTHER → address, phone, email
    ADDRESS_CODE_MASTER  → city name, state name (lookup)
    BRANCH_INFO_MAST     → branch name, branch code, MICR
"""

import base64
from core.logger     import get_logger
from core.exceptions import LumioException
from services.db_service import get_connection

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _blob_to_base64(blob) -> str:
    """
    Converts Oracle BLOB → base64 string.
    Returns empty string if BLOB is None or empty.
    Frontend uses base64 directly in:
    → <img src="data:image/png;base64,..." />
    → watermark background-image
    """
    if blob is None:
        return ""
    try:
        if hasattr(blob, "read"):
            data = blob.read()
        else:
            data = bytes(blob)
        if not data:
            return ""
        return base64.b64encode(data).decode("utf-8")
    except Exception as e:
        logger.warning(f"[BANK] BLOB to base64 failed: {e}")
        return ""


# ═════════════════════════════════════════════════════════════════════════════
# MAIN QUERY
# ═════════════════════════════════════════════════════════════════════════════

BANK_MASTER_QUERY = """
SELECT
    b.BANK_NAME,
    b.BANK_SHORT_NAME,
    b.BANK_LOGO_REPORT,
    b.WATERMARK,
    b.SIGN_1,
    b.CEO_SIGN,
    b.CHAIRMAN_SIGN,
    b.IFSC_CODE,
    b.RBI_REGI_CODE,
    a.ADDRESS_1,
    a.PIN_CODE,
    a.PHONE_1,
    a.FAX_1,
    a.EMAIL_ID,
    c1.CODE_DESCRIPTION    AS CITY,
    c2.CODE_DESCRIPTION    AS STATE,
    br.BRANCH_NAME,
    br.BRANCH_CODE,
    br.MICR_CODE,
    br.IFSC_CODE           AS BRANCH_IFSC
FROM BANK_INFO_MAST b
JOIN ADDRESS_DETAILS_OTHER a
    ON  b.ADDRESS_DET_ID   = a.ADDRESS_DET_ID
JOIN ADDRESS_CODE_MASTER c1
    ON  a.CITY             = c1.CODE_MST_ID
JOIN ADDRESS_CODE_MASTER c2
    ON  a.STATE            = c2.CODE_MST_ID
JOIN BRANCH_INFO_MAST br
    ON  br.BANK_CODE       = b.BANK_CODE
WHERE b.DELETE_FLAG        = 'N'
AND   br.DELETE_FLAG       = 'N'
AND   br.BRANCH_CODE       = :branch_code
AND   ROWNUM               = 1
"""


# ═════════════════════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def fetch_bank_master(
    db_creds,
    branch_code: int = 1,
) -> dict:
    """
    Fetches complete bank + branch info from Oracle.
    Returns clean dict ready to merge into config["bank"].

    Args:
        db_creds    : DBCreds object (host, port, service, user, password)
        branch_code : branch to fetch (default 1 = HO/main branch)
                      TODO: pass actual branch when auth is added

    Returns:
        {
            bankName, bankShortName,
            logo, watermark,
            sign1, ceoSign, chairmanSign,
            address, city, state, pin,
            phone, fax, email,
            ifsc, rbiRegiCode,
            branchName, branchCode,
            micrCode, branchIfsc,
            disclaimer
        }

    Raises:
        LumioException if DB call fails.
    """
    try:
        with get_connection(db_creds) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    BANK_MASTER_QUERY,
                    {"branch_code": branch_code}
                )
                row = cur.fetchone()

                if not row:
                    logger.warning(
                        f"[BANK] No bank master found for "
                        f"branch_code={branch_code}"
                    )
                    return _empty_bank()

                # Map row to column names
                cols = [col[0] for col in cur.description]
                data = dict(zip(cols, row))

                result = {
                    # Bank info
                    "bankName":      data.get("BANK_NAME", ""),
                    "bankShortName": data.get("BANK_SHORT_NAME", ""),
                    "ifsc":          data.get("IFSC_CODE", ""),
                    "rbiRegiCode":   data.get("RBI_REGI_CODE", ""),

                    # Address
                    "address":       data.get("ADDRESS_1", ""),
                    "city":          data.get("CITY", ""),
                    "state":         data.get("STATE", ""),
                    "pin":           data.get("PIN_CODE", ""),
                    "phone":         data.get("PHONE_1", ""),
                    "fax":           data.get("FAX_1", ""),
                    "email":         data.get("EMAIL_ID", ""),

                    # Branch info
                    "branchName":    data.get("BRANCH_NAME", ""),
                    "branchCode":    data.get("BRANCH_CODE", ""),
                    "micrCode":      data.get("MICR_CODE", ""),
                    "branchIfsc":    data.get("BRANCH_IFSC", ""),

                    # BLOBs → base64
                    "logo":          _blob_to_base64(data.get("BANK_LOGO_REPORT")),
                    "watermark":     _blob_to_base64(data.get("WATERMARK")),
                    "sign1":         _blob_to_base64(data.get("SIGN_1")),
                    "ceoSign":       _blob_to_base64(data.get("CEO_SIGN")),
                    "chairmanSign":  _blob_to_base64(data.get("CHAIRMAN_SIGN")),

                    # Disclaimer — hardcoded for now
                    # TODO: store per report type if needed
                    "disclaimer":    "This report is generated for internal use only and is confidential.",
                    "copyright":     f"© {data.get('BANK_NAME', '')}",
                }

                logger.info(
                    f"[BANK] Fetched: {result['bankName']} | "
                    f"branch={result['branchName']}"
                )

                return result

    except LumioException:
        raise
    except Exception as e:
        logger.error(f"[BANK] fetch_bank_master failed: {e}")
        raise LumioException(
            status_code = 500,
            detail      = "Could not fetch bank information from database."
        )


# ═════════════════════════════════════════════════════════════════════════════
# FALLBACK — empty bank dict if fetch fails
# ═════════════════════════════════════════════════════════════════════════════

def _empty_bank() -> dict:
    """
    Returns empty bank dict.
    Used when bank master fetch fails or returns no rows.
    Report still works — just without bank header info.
    """
    return {
        "bankName":      "",
        "bankShortName": "",
        "ifsc":          "",
        "rbiRegiCode":   "",
        "address":       "",
        "city":          "",
        "state":         "",
        "pin":           "",
        "phone":         "",
        "fax":           "",
        "email":         "",
        "branchName":    "",
        "branchCode":    "",
        "micrCode":      "",
        "branchIfsc":    "",
        "logo":          "",
        "watermark":     "",
        "sign1":         "",
        "ceoSign":       "",
        "chairmanSign":  "",
        "disclaimer":    "This report is generated for internal use only and is confidential.",
        "copyright":     "",
    }