"""
services/cache_service.py

Redis cache for column_stats per report.
Prevents recomputing stats on every chat message, insight generation,
and renderer call.

Flow:
    Save   → Redis with TTL expiry
    Read   → Redis first, None if missing or expired
    Clear  → called when user refreshes data from DB

Uses async redis (redis.asyncio) — non-blocking for FastAPI.

Memurai (Redis for Windows) runs on localhost:6379.
Same code works with any Redis server — just update .env.
"""

import json
import redis.asyncio as aioredis

from core.config  import settings
from core.logger  import get_logger
from constants    import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    REDIS_CACHE_TTL,
)

logger = get_logger(__name__)

# ── Key prefix — prevents collision with other apps using same Redis ───────────
KEY_PREFIX = "lumio:stats:"


# ═════════════════════════════════════════════════════════════════════════════
# CONNECTION
# ═════════════════════════════════════════════════════════════════════════════

def _get_client() -> aioredis.Redis:
    """
    Creates a Redis client.
    Called per operation — redis.asyncio handles connection pooling internally.
    No need to manage connection lifecycle manually.
    """
    return aioredis.Redis(
        host     = REDIS_HOST,
        port     = REDIS_PORT,
        password = REDIS_PASSWORD or None,
        decode_responses = True,   # returns str not bytes
    )


# ═════════════════════════════════════════════════════════════════════════════
# KEY BUILDER
# ═════════════════════════════════════════════════════════════════════════════

def _key(bank_id: str, report_id: str) -> str:
    """
    Builds Redis key.
    Bank isolated — Bank A stats never mix with Bank B.

    Format: lumio:stats:{bank_id}:{report_id}
    Example: lumio:stats:vgipl:NPA_REPORT_2024
    """
    return f"{KEY_PREFIX}{bank_id.lower()}:{report_id}"


# ═════════════════════════════════════════════════════════════════════════════
# SAVE
# ═════════════════════════════════════════════════════════════════════════════

async def save_stats(
    bank_id:   str,
    report_id: str,
    stats:     dict,
    ttl:       int = None,
) -> bool:
    """
    Saves column_stats to Redis with TTL expiry.

    Args:
        bank_id   : bank identifier (for key isolation)
        report_id : report identifier
        stats     : column_stats dict from db_service
        ttl       : seconds until expiry (default from constants)

    Returns:
        True if saved, False if Redis unavailable.
    """
    if not stats:
        logger.warning(f"[CACHE] Empty stats — not saving: {report_id}")
        return False

    ttl    = ttl or REDIS_CACHE_TTL
    key    = _key(bank_id, report_id)
    client = _get_client()

    try:
        await client.set(
            key,
            json.dumps(stats, ensure_ascii=False),
            ex = ttl,
        )
        logger.info(
            f"[CACHE] Saved: {key} | "
            f"ttl={ttl}s | "
            f"cols={len(stats)}"
        )
        return True

    except Exception as e:
        logger.error(f"[CACHE] Save failed: {key} | {e}")
        return False

    finally:
        await client.aclose()


# ═════════════════════════════════════════════════════════════════════════════
# GET
# ═════════════════════════════════════════════════════════════════════════════

async def get_stats(
    bank_id:   str,
    report_id: str,
) -> dict | None:
    """
    Reads column_stats from Redis.

    Returns:
        dict  → cache hit, stats ready to use
        None  → cache miss or expired, caller must recompute
    """
    key    = _key(bank_id, report_id)
    client = _get_client()

    try:
        raw = await client.get(key)

        if raw is None:
            logger.info(f"[CACHE] Miss: {key}")
            return None

        stats = json.loads(raw)
        logger.info(
            f"[CACHE] Hit: {key} | "
            f"cols={len(stats)}"
        )
        return stats

    except Exception as e:
        logger.error(f"[CACHE] Get failed: {key} | {e}")
        return None

    finally:
        await client.aclose()


# ═════════════════════════════════════════════════════════════════════════════
# CLEAR
# ═════════════════════════════════════════════════════════════════════════════

async def clear_stats(
    bank_id:   str,
    report_id: str,
) -> bool:
    """
    Clears cached stats for a report.
    Called when user refreshes data from DB — old stats are stale.

    Returns:
        True if cleared, False if Redis unavailable.
    """
    key    = _key(bank_id, report_id)
    client = _get_client()

    try:
        deleted = await client.delete(key)
        if deleted:
            logger.info(f"[CACHE] Cleared: {key}")
        else:
            logger.info(f"[CACHE] Nothing to clear: {key}")
        return True

    except Exception as e:
        logger.error(f"[CACHE] Clear failed: {key} | {e}")
        return False

    finally:
        await client.aclose()


# ═════════════════════════════════════════════════════════════════════════════
# CLEAR ALL FOR BANK (admin use)
# ═════════════════════════════════════════════════════════════════════════════

async def clear_all_stats(bank_id: str) -> int:
    """
    Clears ALL cached stats for a bank.
    Admin use only — e.g. after major DB migration.

    Returns:
        Number of keys cleared.
    """
    pattern = f"{KEY_PREFIX}{bank_id.lower()}:*"
    client  = _get_client()

    try:
        keys    = await client.keys(pattern)
        if not keys:
            logger.info(f"[CACHE] No keys found for bank: {bank_id}")
            return 0

        deleted = await client.delete(*keys)
        logger.info(
            f"[CACHE] Cleared all for bank: {bank_id} | "
            f"keys={deleted}"
        )
        return deleted

    except Exception as e:
        logger.error(f"[CACHE] Clear all failed for {bank_id}: {e}")
        return 0

    finally:
        await client.aclose()


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═════════════════════════════════════════════════════════════════════════════

async def ping() -> bool:
    """
    Checks if Redis/Memurai is reachable.
    Called at app startup to warn if cache is unavailable.
    App still works without Redis — just slower (recomputes stats every time).
    """
    client = _get_client()
    try:
        result = await client.ping()
        if result:
            logger.info("[CACHE] Redis/Memurai connected ✅")
        return result

    except Exception as e:
        logger.warning(
            f"[CACHE] Redis/Memurai not reachable: {e} | "
            f"App will recompute stats on every request."
        )
        return False

    finally:
        await client.aclose()