"""
manager/config.py
══════════════════════════════════════════════════════════════════════════════
All configuration for the Manager bot, loaded from environment variables.
══════════════════════════════════════════════════════════════════════════════
"""

import os


def _int_list(val: str, default: str = "") -> list[int]:
    """Parse a space- or comma-separated list of integers."""
    raw = val or default
    parts = raw.replace(",", " ").split()
    result = []
    for p in parts:
        try:
            result.append(int(p.strip()))
        except ValueError:
            pass
    return result


class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    API_ID         = int(os.environ.get("API_ID", "20140875"))
    API_HASH       = os.environ.get("API_HASH", "a06fa97d5a853ec2da79015b11335a17")
    BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
    BOT_MAX_SIZE = int(os.environ.get("BOT_MAX_SIZE", str(2000 * 1024 * 1024)))
    # Shared String Session — used for reading/writing Worker Control Group
    # protocol messages. NEVER used to download or upload files.
    STRING_SESSION = os.environ.get("STRING_SESSION", "")

    # ── Channels / Groups ─────────────────────────────────────────────────────
    WORKER_CONTROL_GROUP_ID  = int(os.environ.get("WORKER_CONTROL_GROUP_ID",  "-1004454437880"))
    WORKER_OUTPUT_CHANNEL_ID = int(os.environ.get("WORKER_OUTPUT_CHANNEL_ID", "-1004488266962"))
    CENTRAL_DUMP_CHANNEL_ID  = int(os.environ.get("CENTRAL_DUMP_CHANNEL_ID",  "-1003715198652"))
    LOG_CHANNEL              = int(os.environ.get("LOG_CHANNEL", "-1004427602817"))

    # ── Database ──────────────────────────────────────────────────────────────
    MONGO_URI = os.environ.get("MONGO_URI", "")
    DB_NAME   = os.environ.get("DB_NAME", "DistributedRenameBot")

    # ── Owners / Admins ───────────────────────────────────────────────────────
    # Multiple owner IDs — space or comma-separated
    OWNER_IDS: list[int] = _int_list(
        os.environ.get("OWNER_IDS", ""),
        default="6672752177 1828405916",
    )

    # ── Worker settings ───────────────────────────────────────────────────────
    WORKER_OFFLINE_TIMEOUT = int(os.environ.get("WORKER_OFFLINE_TIMEOUT", "120"))  # seconds
    JOB_LEASE_SECONDS      = int(os.environ.get("JOB_LEASE_SECONDS", "300"))       # 5 min

    # ── ImgBB (thumbnail external storage) ───────────────────────────────────
    IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "7c884ffafafa0846a595d70b373be802")

    # ── Health check ─────────────────────────────────────────────────────────
    PORT = int(os.environ.get("PORT", "8080"))

    # ── UI ────────────────────────────────────────────────────────────────────
    START_PIC      = os.environ.get("START_PIC", "https://ibb.co/hP6TSh3")
    SETTINGS_IMAGE = os.environ.get("SETTINGS_IMAGE", "https://ibb.co/hP6TSh3")

 
    BOT_UPTIME = __import__("time").time()
