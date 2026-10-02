"""
manager/helper/database.py
══════════════════════════════════════════════════════════════════════════════
Manager-side MongoDB layer.

Collections
───────────
  users           — per-user settings (thumbnail URL, caption, prefix, suffix,
                    metadata fields, premium, rename_mode, format_template,
                    rename_source, auto_media_type, dump_channel)
  batches         — one batch per /autorename … /done cycle
  jobs            — one document per file; full job lifecycle
  workers         — registered worker bots and their live state
  global_settings — owner-controlled settings (global metadata, etc.)
  statistics      — aggregate counters (non-blocking)
  deliveries      — delivery records for idempotency

Design principles
─────────────────
  • Atomic transitions:  update_one with $set + filter on current state
    prevents duplicate processing across restarts.
  • Lease mechanism:  jobs have lease_until; expired leases → QUEUED again.
  • Metadata snapshot:  job stores metadata_version at creation time so an
    owner metadata change never mid-flight affects existing jobs.
  • One DB for everything — Manager is authoritative; Workers read/write only
    what the protocol explicitly requires (state updates, statistics).
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import motor.motor_asyncio
from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
_D_AUTO_DAILY    = 30
_D_MANUAL_DAILY  = 10
_D_CONCURRENCY   = 4
_D_LEASE_SECONDS = 300   # 5 min; worker must ACK within this window

_META_DEFAULTS = {
    "title": "", "author": "", "artist": "",
    "audio": "", "video": "", "subtitle": "", "comment": "",
}


class ManagerDB:
    """Async MongoDB wrapper for the Manager bot."""

    def __init__(self, uri: str, db_name: str):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self._db     = self._client[db_name]

        # Collections
        self.users    = self._db["users"]
        self.batches  = self._db["batches"]
        self.jobs     = self._db["jobs"]
        self.workers  = self._db["workers"]
        self.settings = self._db["global_settings"]
        self.stats    = self._db["statistics"]
        self.deliveries = self._db["deliveries"]

    # ──────────────────────────────────────────────────────────────────────────
    # Indexes  (idempotent — safe to call every boot)
    # ──────────────────────────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        # jobs
        await self.jobs.create_index([("job_id",    ASCENDING)], unique=True)
        await self.jobs.create_index([("batch_id",  ASCENDING)])
        await self.jobs.create_index([("user_id",   ASCENDING)])
        await self.jobs.create_index([("worker_id", ASCENDING)])
        await self.jobs.create_index([("status",    ASCENDING)])
        await self.jobs.create_index([("lease_until", ASCENDING)])
        await self.jobs.create_index([("created_at", ASCENDING)])

        # batches
        await self.batches.create_index([("batch_id", ASCENDING)], unique=True)
        await self.batches.create_index([("user_id",  ASCENDING)])
        await self.batches.create_index([("status",   ASCENDING)])

        # workers
        await self.workers.create_index([("worker_id",      ASCENDING)], unique=True)
        await self.workers.create_index([("last_heartbeat",  ASCENDING)])
        await self.workers.create_index([("status",          ASCENDING)])

        # deliveries (idempotency)
        await self.deliveries.create_index([("job_id", ASCENDING)], unique=True)

        # users
        await self.users.create_index([("_id", ASCENDING)])

        logger.info("[db] Indexes ensured")

    # ══════════════════════════════════════════════════════════════════════════
    # USER SETTINGS
    # ══════════════════════════════════════════════════════════════════════════

    def _new_user(self, user_id: int) -> dict:
        return {
            "_id":              int(user_id),
            "thumbnail_url":    None,   # ImgBB HTTPS URL or None
            "caption":          None,
            "prefix":           None,
            "suffix":           None,
            "metadata":         False,
            "metadata_fields":  dict(_META_DEFAULTS),
            "dump_channel":     None,
            "dump_mode":        False,
            "rename_mode":      "manual",
            "format_template":  None,
            "rename_source":    "filename",
            "auto_media_type":  None,
            "premium":          False,
            "premium_expiry":   None,
        }

    async def get_user(self, user_id: int) -> dict:
        doc = await self.users.find_one({"_id": int(user_id)})
        if not doc:
            doc = self._new_user(user_id)
        return doc

    async def _set(self, user_id: int, field: str, value: Any) -> None:
        await self.users.update_one(
            {"_id": int(user_id)},
            {"$set": {field: value}},
            upsert=True,
        )

    async def _get(self, user_id: int, field: str, default=None) -> Any:
        doc = await self.users.find_one({"_id": int(user_id)}, {field: 1})
        return (doc or {}).get(field, default)

    # Per-user settings helpers
    async def get_format_template(self, user_id: int) -> str | None:
        return await self._get(user_id, "format_template")

    async def set_format_template(self, user_id: int, tpl: str) -> None:
        await self._set(user_id, "format_template", tpl)

    async def get_rename_source(self, user_id: int) -> str:
        return await self._get(user_id, "rename_source", "filename")

    async def set_rename_source(self, user_id: int, src: str) -> None:
        await self._set(user_id, "rename_source", src)

    async def get_rename_mode(self, user_id: int) -> str:
        return await self._get(user_id, "rename_mode", "manual")

    async def set_rename_mode(self, user_id: int, mode: str) -> None:
        await self._set(user_id, "rename_mode", mode)

    async def get_prefix(self, user_id: int) -> str:
        return await self._get(user_id, "prefix", "") or ""

    async def set_prefix(self, user_id: int, val: str) -> None:
        await self._set(user_id, "prefix", val)

    async def get_suffix(self, user_id: int) -> str:
        return await self._get(user_id, "suffix", "") or ""

    async def set_suffix(self, user_id: int, val: str) -> None:
        await self._set(user_id, "suffix", val)

    async def get_thumbnail_url(self, user_id: int) -> str | None:
        return await self._get(user_id, "thumbnail_url")

    async def set_thumbnail_url(self, user_id: int, url: str | None) -> None:
        await self._set(user_id, "thumbnail_url", url)

    async def get_caption(self, user_id: int) -> str | None:
        return await self._get(user_id, "caption")

    async def set_caption(self, user_id: int, cap: str | None) -> None:
        await self._set(user_id, "caption", cap)

    async def get_media_preference(self, user_id: int) -> str | None:
        return await self._get(user_id, "auto_media_type")

    async def set_media_preference(self, user_id: int, pref: str) -> None:
        await self._set(user_id, "auto_media_type", pref)

    # ── Premium ───────────────────────────────────────────────────────────────

    async def is_premium(self, user_id: int, admin_ids: list[int] = None) -> bool:
        if admin_ids and int(user_id) in admin_ids:
            return True
        doc = await self.users.find_one(
            {"_id": int(user_id)}, {"premium": 1, "premium_expiry": 1}
        )
        if not doc or not doc.get("premium"):
            return False
        expiry = doc.get("premium_expiry")
        return expiry is None or expiry >= time.time()

    async def grant_premium(self, user_id: int, expiry_ts: float | None = None) -> None:
        await self.users.update_one(
            {"_id": int(user_id)},
            {"$set": {"premium": True, "premium_expiry": expiry_ts}},
            upsert=True,
        )

    async def revoke_premium(self, user_id: int) -> None:
        await self.users.update_one(
            {"_id": int(user_id)},
            {"$set": {"premium": False, "premium_expiry": None}},
            upsert=True,
        )

    # ── Ban list ──────────────────────────────────────────────────────────────

    async def is_banned(self, user_id: int) -> bool:
        doc = await self.users.find_one({"_id": int(user_id)}, {"banned": 1})
        return bool((doc or {}).get("banned"))

    async def ban_user(self, user_id: int) -> None:
        await self.users.update_one(
            {"_id": int(user_id)}, {"$set": {"banned": True}}, upsert=True
        )

    async def unban_user(self, user_id: int) -> None:
        await self.users.update_one(
            {"_id": int(user_id)}, {"$set": {"banned": False}}, upsert=True
        )

    # ── Pipeline settings bulk-load (one round-trip per job) ─────────────────

    async def get_pipeline_settings(self, user_id: int, admin_ids: list[int] = None) -> dict:
        """Return all settings needed when creating a job snapshot."""
        proj = {
            "thumbnail_url": 1, "caption": 1, "prefix": 1, "suffix": 1,
            "metadata": 1, "metadata_fields": 1,
            "dump_channel": 1, "dump_mode": 1,
            "rename_mode": 1, "format_template": 1, "rename_source": 1,
            "auto_media_type": 1, "premium": 1, "premium_expiry": 1,
        }
        doc = await self.users.find_one({"_id": int(user_id)}, proj) or {}

        is_admin   = bool(admin_ids and int(user_id) in admin_ids)
        is_premium = is_admin
        if not is_premium and doc.get("premium"):
            expiry = doc.get("premium_expiry")
            is_premium = (expiry is None or expiry >= time.time())

        return {
            "thumbnail_url":    doc.get("thumbnail_url"),
            "caption":          doc.get("caption"),
            "prefix":           doc.get("prefix") or "",
            "suffix":           doc.get("suffix") or "",
            "metadata":         bool(doc.get("metadata")),
            "metadata_fields":  doc.get("metadata_fields") or dict(_META_DEFAULTS),
            "dump_channel":     doc.get("dump_channel"),
            "dump_mode":        bool(doc.get("dump_mode")),
            "rename_mode":      doc.get("rename_mode", "manual"),
            "format_template":  doc.get("format_template"),
            "rename_source":    doc.get("rename_source", "filename"),
            "auto_media_type":  doc.get("auto_media_type"),
            "premium":          is_premium,
            "is_admin":         is_admin,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # GLOBAL SETTINGS  (owner-controlled)
    # ══════════════════════════════════════════════════════════════════════════

    async def get_global_metadata(self) -> dict:
        doc = await self.settings.find_one({"_id": "global_metadata"}) or {}
        return {
            "enabled":  bool(doc.get("enabled", False)),
            "title":    doc.get("title", ""),
            "author":   doc.get("author", ""),
            "artist":   doc.get("artist", ""),
            "comment":  doc.get("comment", ""),
            "audio":    doc.get("audio", ""),
            "video":    doc.get("video", ""),
            "subtitle": doc.get("subtitle", ""),
        }

    async def set_global_metadata(self, fields: dict) -> int:
        """Set global metadata fields and increment version. Returns new version."""
        result = await self.settings.find_one_and_update(
            {"_id": "global_metadata"},
            {"$set": fields, "$inc": {"version": 1}},
            upsert=True,
            return_document=True,
        )
        return int((result or {}).get("version", 1))

    async def get_metadata_version(self) -> int:
        doc = await self.settings.find_one({"_id": "global_metadata"}, {"version": 1})
        return int((doc or {}).get("version", 0))

    async def get_setting(self, key: str, default: Any = None) -> Any:
        doc = await self.settings.find_one({"_id": key})
        return (doc or {}).get("value", default)

    async def set_setting(self, key: str, value: Any) -> None:
        await self.settings.update_one(
            {"_id": key}, {"$set": {"value": value}}, upsert=True
        )

    # ══════════════════════════════════════════════════════════════════════════
    # WORKERS
    # ══════════════════════════════════════════════════════════════════════════

    async def upsert_worker(self, worker_id: str, data: dict) -> None:
        """Idempotent worker registration / update."""
        now = time.time()
        await self.workers.update_one(
            {"worker_id": worker_id},
            {
                "$set":         {**data, "worker_id": worker_id},
                "$setOnInsert": {"registered_at": now, "completed_jobs": 0, "failed_jobs": 0},
            },
            upsert=True,
        )

    async def update_heartbeat(
        self, worker_id: str, active_jobs: int, capacity: int, status: str
    ) -> None:
        await self.workers.update_one(
            {"worker_id": worker_id},
            {"$set": {
                "last_heartbeat": time.time(),
                "active_jobs":    active_jobs,
                "capacity":       capacity,
                "status":         status,
            }},
            upsert=True,
        )

    async def set_worker_flood_wait(self, worker_id: str, until_ts: float) -> None:
        await self.workers.update_one(
            {"worker_id": worker_id},
            {"$set": {
                "status":            "FLOOD_WAIT",
                "flood_wait_until":  until_ts,
            }},
        )

    async def get_available_workers(self) -> list[dict]:
        """
        Return workers that:
          • are ONLINE or BUSY
          • have active_jobs < capacity
          • are not in FLOOD_WAIT (or flood_wait_until has passed)
        """
        now = time.time()
        cursor = self.workers.find({
            "status":      {"$in": ["ONLINE", "BUSY"]},
            "$expr":       {"$lt": ["$active_jobs", "$capacity"]},
            "$or": [
                {"flood_wait_until": {"$exists": False}},
                {"flood_wait_until": {"$lte": now}},
            ],
        })
        return await cursor.to_list(length=100)

    async def get_all_workers(self) -> list[dict]:
        return await self.workers.find({}).to_list(length=200)

    async def get_worker(self, worker_id: str) -> dict | None:
        return await self.workers.find_one({"worker_id": worker_id})

    async def mark_workers_offline(self, timeout_seconds: int) -> int:
        """Mark workers whose last heartbeat is older than timeout as OFFLINE."""
        cutoff = time.time() - timeout_seconds
        result = await self.workers.update_many(
            {
                "last_heartbeat": {"$lt": cutoff},
                "status":         {"$ne": "OFFLINE"},
            },
            {"$set": {"status": "OFFLINE"}},
        )
        return result.modified_count

    async def inc_worker_active(self, worker_id: str) -> None:
        """Increment active_jobs by 1 when manager assigns a job (immediate slot tracking)."""
        await self.workers.update_one(
            {"worker_id": worker_id},
            {"$inc": {"active_jobs": 1}},
        )

    async def dec_worker_active(self, worker_id: str) -> None:
        """Decrement active_jobs by 1 when a job finishes/fails (immediate slot release)."""
        await self.workers.update_one(
            {"worker_id": worker_id},
            [{"$set": {"active_jobs": {"$max": [0, {"$subtract": ["$active_jobs", 1]}]}}}],
        )

    async def inc_worker_completed(self, worker_id: str) -> None:
        await self.workers.update_one(
            {"worker_id": worker_id}, {"$inc": {"completed_jobs": 1}}
        )

    async def inc_worker_failed(self, worker_id: str) -> None:
        await self.workers.update_one(
            {"worker_id": worker_id}, {"$inc": {"failed_jobs": 1}}
        )

    # ══════════════════════════════════════════════════════════════════════════
    # BATCHES
    # ══════════════════════════════════════════════════════════════════════════

    async def create_batch(self, batch_id: str, user_id: int, rename_pattern: str) -> None:
        now = time.time()
        await self.batches.update_one(
            {"batch_id": batch_id},
            {"$setOnInsert": {
                "batch_id":       batch_id,
                "user_id":        int(user_id),
                "rename_pattern": rename_pattern,
                "status":         "OPEN",
                "created_at":     now,
                "closed_at":      None,
                "file_count":     0,
                "done_count":     0,
                "failed_count":   0,
            }},
            upsert=True,
        )

    async def get_active_batch(self, user_id: int) -> dict | None:
        """Return the user's currently OPEN batch, or None."""
        return await self.batches.find_one(
            {"user_id": int(user_id), "status": "OPEN"}
        )

    async def close_batch(self, batch_id: str, user_id: int) -> bool:
        """Atomically close the batch. Returns True if it was open and is now closed."""
        result = await self.batches.update_one(
            {"batch_id": batch_id, "user_id": int(user_id), "status": "OPEN"},
            {"$set": {"status": "CLOSED", "closed_at": time.time()}},
        )
        return result.modified_count > 0

    async def inc_batch_file_count(self, batch_id: str) -> None:
        await self.batches.update_one(
            {"batch_id": batch_id}, {"$inc": {"file_count": 1}}
        )

    async def inc_batch_done(self, batch_id: str) -> None:
        await self.batches.update_one(
            {"batch_id": batch_id}, {"$inc": {"done_count": 1}}
        )

    async def inc_batch_failed(self, batch_id: str) -> None:
        await self.batches.update_one(
            {"batch_id": batch_id}, {"$inc": {"failed_count": 1}}
        )

    # ══════════════════════════════════════════════════════════════════════════
    # JOBS
    # ══════════════════════════════════════════════════════════════════════════

    async def create_job(self, job: dict) -> None:
        """
        Insert a new job document. Fields expected:
          job_id, batch_id, user_id, source_chat_id, source_message_id,
          rename_pattern, prefix, suffix, metadata, metadata_version,
          thumbnail_url, dump_enabled, original_filename, file_size, queued_at
        """
        job.setdefault("status",     "QUEUED")
        job.setdefault("worker_id",  None)
        job.setdefault("created_at", time.time())
        job.setdefault("assigned_at", None)
        job.setdefault("lease_until", None)
        await self.jobs.insert_one(job)

    async def get_job(self, job_id: str) -> dict | None:
        return await self.jobs.find_one({"job_id": job_id})

    async def get_queued_jobs(self, limit: int = 100) -> list[dict]:
        """Return QUEUED jobs ordered by user_id (for fair scheduling) then created_at."""
        cursor = self.jobs.find(
            {"status": "QUEUED"},
            sort=[("user_id", ASCENDING), ("created_at", ASCENDING)],
            limit=limit,
        )
        return await cursor.to_list(length=limit)

    async def atomic_assign_job(
        self,
        job_id:    str,
        worker_id: str,
        lease_sec: int = _D_LEASE_SECONDS,
    ) -> bool:
        """
        Atomically transition job from QUEUED → ASSIGNED and set lease.
        Returns True if the transition happened (job was QUEUED and is now ours).
        """
        now = time.time()
        result = await self.jobs.update_one(
            {"job_id": job_id, "status": "QUEUED"},
            {"$set": {
                "status":      "ASSIGNED",
                "worker_id":   worker_id,
                "assigned_at": now,
                "lease_until": now + lease_sec,
            }},
        )
        return result.modified_count > 0

    async def transition_job_state(
        self,
        job_id:   str,
        from_state: str,
        to_state:   str,
        extra:      dict | None = None,
    ) -> bool:
        """
        Atomically move a job from from_state → to_state.
        Returns True if the transition happened.
        """
        update: dict = {"$set": {"status": to_state}}
        if extra:
            update["$set"].update(extra)
        result = await self.jobs.update_one(
            {"job_id": job_id, "status": from_state},
            update,
        )
        return result.modified_count > 0

    async def set_job_state(self, job_id: str, state: str, extra: dict | None = None) -> None:
        """Unconditional state set (use only for terminal states from correct caller)."""
        update = {"status": state}
        if extra:
            update.update(extra)
        await self.jobs.update_one({"job_id": job_id}, {"$set": update})

    async def requeue_expired_leases(self) -> int:
        """
        Move ASSIGNED/ACKED jobs whose lease has expired back to QUEUED.
        Returns number of jobs requeued.
        """
        now = time.time()
        result = await self.jobs.update_many(
            {
                "status":      {"$in": ["ASSIGNED", "ACKED"]},
                "lease_until": {"$lte": now},
            },
            {"$set": {
                "status":      "QUEUED",
                "worker_id":   None,
                "assigned_at": None,
                "lease_until": None,
            }},
        )
        if result.modified_count:
            logger.info("[db] Requeued %d expired-lease jobs", result.modified_count)
        return result.modified_count

    async def get_user_active_jobs(self, user_id: int) -> list[dict]:
        """Jobs for a user that are not yet DONE or FAILED."""
        from shared.protocol import TERMINAL_STATES
        cursor = self.jobs.find({
            "user_id": int(user_id),
            "status":  {"$nin": list(TERMINAL_STATES)},
        })
        return await cursor.to_list(length=200)

    async def get_batch(self, batch_id: str) -> dict | None:
        """Fetch a batch document by its ID regardless of status."""
        return await self.batches.find_one({"batch_id": batch_id})

    async def get_batch_jobs(self, batch_id: str) -> list[dict]:
        return await self.jobs.find({"batch_id": batch_id}).to_list(length=1000)

    # ══════════════════════════════════════════════════════════════════════════
    # DELIVERIES  (idempotency guard)
    # ══════════════════════════════════════════════════════════════════════════

    async def record_delivery(
        self,
        job_id:    str,
        user_id:   int,
        filename:  str,
    ) -> bool:
        """
        Record a delivery atomically.
        Returns True if this is the FIRST time — caller should deliver.
        Returns False if already recorded — caller must skip (idempotency).
        """
        try:
            await self.deliveries.insert_one({
                "job_id":       job_id,
                "user_id":      int(user_id),
                "filename":     filename,
                "delivered_at": time.time(),
            })
            return True
        except Exception:
            # DuplicateKeyError → already delivered
            return False

    async def was_delivered(self, job_id: str) -> bool:
        doc = await self.deliveries.find_one({"job_id": job_id})
        return doc is not None

    # ══════════════════════════════════════════════════════════════════════════
    # STATISTICS  (non-blocking increments)
    # ══════════════════════════════════════════════════════════════════════════

    async def inc_stat(self, key: str, amount: int = 1) -> None:
        try:
            await self.stats.update_one(
                {"_id": key}, {"$inc": {"value": amount}}, upsert=True
            )
        except Exception as e:
            logger.debug("[db] inc_stat failed (non-critical): %s", e)

    async def get_stat(self, key: str) -> int:
        doc = await self.stats.find_one({"_id": key})
        return int((doc or {}).get("value", 0))

    async def get_leaderboard(self, limit: int = 10) -> list[dict]:
        cursor = self.stats.find(
            {"_id": {"$regex": r"^user_done_"}},
            sort=[("value", DESCENDING)],
            limit=limit,
        )
        return await cursor.to_list(length=limit)

    # ══════════════════════════════════════════════════════════════════════════
    # DAILY RENAME COUNTERS
    # ══════════════════════════════════════════════════════════════════════════

    async def get_auto_daily_count(self, user_id: int) -> int:
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        doc   = await self.users.find_one({"_id": int(user_id)}, {"auto_rename_daily": 1})
        return int(((doc or {}).get("auto_rename_daily") or {}).get(today, 0))

    async def inc_auto_daily(self, user_id: int) -> None:
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        await self.users.update_one(
            {"_id": int(user_id)},
            {"$inc": {f"auto_rename_daily.{today}": 1}},
            upsert=True,
        )

    async def get_auto_daily_limit(self) -> int:
        return int(await self.get_setting("auto_daily_limit", _D_AUTO_DAILY))

    async def set_auto_daily_limit(self, n: int) -> None:
        await self.set_setting("auto_daily_limit", n)


# ── Module-level singleton (initialised by manager_bot.py) ───────────────────
_db: ManagerDB | None = None


def init_db(uri: str, db_name: str) -> ManagerDB:
    global _db
    _db = ManagerDB(uri, db_name)
    return _db


def get_db() -> ManagerDB:
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db
