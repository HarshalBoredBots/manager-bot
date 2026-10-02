"""
manager/helper/staging.py
FloodWait-safe staging service. Single point for all user -> output channel copies.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from pyrogram.errors import FloodWait

log = logging.getLogger(__name__)

STAGING_CONCURRENCY = int(os.environ.get("STAGING_CONCURRENCY", "4"))
_MAX_ATTEMPTS = 5

_sem = asyncio.Semaphore(STAGING_CONCURRENCY)
_cooldown_lock = asyncio.Lock()
_cooldown_until = 0.0
_seen: dict[tuple[int, int], tuple[int, int]] = {}


async def _wait_cooldown() -> None:
    async with _cooldown_lock:
        remain = _cooldown_until - time.time()
    if remain > 0:
        await asyncio.sleep(remain)


async def _set_cooldown(sec: float) -> None:
    global _cooldown_until
    async with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, time.time() + sec)


async def stage_to_output_channel(message, output_channel_id: int, job_id: str = ""):
    """
    Copy `message` to the worker output channel with FloodWait backoff.
    Deduplicates on (chat_id, message_id). Returns the copied Message.
    """
    key = (message.chat.id, message.id)
    if key in _seen:
        c, m = _seen[key]
        log.info("[stage] job=%s reusing staged=%s/%s", job_id, c, m)
        return await message._client.get_messages(c, m)

    async with _sem:
        last_exc = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            await _wait_cooldown()
            try:
                copied = await message.copy(output_channel_id)
                await asyncio.sleep(0.15)
                _seen[key] = (copied.chat.id, copied.id)
                log.info("[stage] job=%s ok attempt=%d staged=%s/%s",
                         job_id, attempt, copied.chat.id, copied.id)
                return copied
            except FloodWait as fw:
                last_exc = fw
                wait = int(getattr(fw, "value", 30) or 30) + 2
                log.warning("[stage] job=%s FloodWait=%ds attempt=%d/%d",
                            job_id, wait, attempt, _MAX_ATTEMPTS)
                if attempt >= _MAX_ATTEMPTS:
                    break
                await _set_cooldown(wait)
            except Exception as exc:
                last_exc = exc
                log.warning("[stage] job=%s err=%s attempt=%d/%d",
                            job_id, type(exc).__name__, attempt, _MAX_ATTEMPTS)
                if attempt >= _MAX_ATTEMPTS:
                    break
                await asyncio.sleep(1.5 * attempt)
        raise RuntimeError(f"staging failed after {_MAX_ATTEMPTS} attempts: {last_exc}")


async def validate_output_channel(client, channel_id: int) -> bool:
    """Startup probe -- verifies the manager bot can post to the channel."""
    try:
        me = await client.get_chat_member(channel_id, "me")

        # FIX BUG 10: In Pyrogram 2.x the ChatMemberStatus enum's .value may
        # include the full enum name ("ChatMemberStatus.administrator") rather
        # than just "administrator", making a bare .value comparison unreliable.
        # Use .name (always just the identifier, e.g. "administrator") and also
        # fall back to lowercased str() to handle any Pyrogram version.
        status_name = me.status.name.lower() if hasattr(me.status, "name") else str(me.status).lower()
        ok = status_name in ("administrator", "creator")
        if ok and status_name == "administrator":
            ok = bool(me.privileges and me.privileges.can_post_messages)
        log.info("[stage] output_channel=%s writable=%s", channel_id, ok)
        return ok
    except Exception as exc:
        log.error("[stage] output_channel=%s probe failed: %s", channel_id, exc)
        return False
