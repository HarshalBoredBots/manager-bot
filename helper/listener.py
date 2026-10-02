"""
manager/helper/listener.py
══════════════════════════════════════════════════════════════════════════════
Manager-side String Session listener.

The shared String Session joins the Worker Control Group and reads every
message posted there.  It dispatches on "type" field to the appropriate
handler in this module.

Architecture rule: the String Session NEVER downloads or uploads files.
It only reads protocol messages and routes them to handler coroutines,
which update MongoDB and trigger Telegram deliveries via the Manager Bot
client (not the String Session).

Handler map
───────────
  REGISTER  → _handle_register
  HEARTBEAT → _handle_heartbeat
  ACK       → _handle_ack
  STATE     → _handle_state
  RESULT    → _handle_result
  FAILED    → _handle_failed
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from pyrogram import filters
from pyrogram.types import Message

from shared.protocol import (
    decode, is_proto, require_fields,
    MSG_REGISTER, MSG_HEARTBEAT, MSG_ACK, MSG_STATE, MSG_RESULT, MSG_FAILED,
    STATE_ACKED, STATE_DONE, STATE_FAILED,
    WORKER_ONLINE, WORKER_BUSY,
)

if TYPE_CHECKING:
    from pyrogram import Client
    from helper.database import ManagerDB

logger = logging.getLogger(__name__)


# ── Delivery pacing ─────────────────────────────────────────────
_delivery_lock = asyncio.Lock()
_user_last_delivery: dict[int, float] = {}
_MIN_GAP = 0.8


async def _pace_delivery(user_id: int) -> None:
    async with _delivery_lock:
        prev = _user_last_delivery.get(user_id, 0.0)
        gap  = time.time() - prev
        if gap < _MIN_GAP:
            await asyncio.sleep(_MIN_GAP - gap)
        _user_last_delivery[user_id] = time.time()



class ProtocolListener:
    """
    Attaches to the String Session client and handles all incoming worker
    messages from the Worker Control Group.
    """

    def __init__(
        self,
        session_client,           # Pyrogram Client (String Session)
        bot_client,               # Pyrogram Client (Manager Bot)
        db: "ManagerDB",
        control_group_id: int,
        output_channel_id: int,
        dump_channel_id: int,
        admin_ids: list[int],
    ):
        self._session    = session_client
        self._bot        = bot_client
        self._db         = db
        self._group_id   = control_group_id
        self._output_ch  = output_channel_id
        self._dump_ch    = dump_channel_id
        self._admins     = admin_ids

        # FIX BUG 1 & BUG 3: Register the handler on the session client here,
        # inside __init__, NOT inside _owns_job (which had dead code after return).
        self._session.add_handler(
            self._message_handler(),
        )

    async def _owns_job(self, job_id: str, worker_id: str) -> bool:
        # FIX BUG 1: Removed dead code (add_handler call) that appeared after
        # this return statement. It was unreachable and the handler was never
        # being registered, breaking the entire listener.
        job = await self._db.get_job(job_id)
        return bool(job and job.get("worker_id") == worker_id)

    def _message_handler(self):
        """
        Build a Pyrogram message handler for the control group.

        IMPORTANT: filters.chat() does not work reliably on user-account
        String Sessions in Pyrogram 2.0.106.  We use filters.all and check
        the chat ID manually so the user session receives all messages and
        filters in Python-space.
        """
        from pyrogram.handlers import MessageHandler

        async def _on_message(client, message: Message):
            # Manual group ID check — reliable on both bot and user sessions
            chat_id = getattr(message.chat, "id", None)
            if chat_id != self._group_id:
                return
            text = message.text or message.caption or ""
            if not is_proto(text):
                return
            msg = decode(text)
            if msg is None:
                logger.debug("[listener] Malformed proto message — ignored")
                return
            await self._dispatch(msg)

        return MessageHandler(_on_message, filters=filters.all)

    async def _check_session_identity(self, worker_session_client) -> None:
        """
        Warn loudly if the Manager's String Session is the same Telegram account
        as the worker's — in that case Telegram won't echo messages back and
        REGISTER/HEARTBEAT will silently never arrive.
        """
        try:
            manager_me = await self._session.get_me()
            logger.info(
                "[listener] Manager String Session account: %s (id=%s) — "
                "Worker String Session MUST use a DIFFERENT Telegram account, "
                "otherwise REGISTER/HEARTBEAT messages will never be received.",
                manager_me.first_name, manager_me.id,
            )
        except Exception as exc:
            logger.debug("[listener] Could not fetch session identity: %s", exc)

    async def _dispatch(self, msg: dict) -> None:
        """Route a decoded protocol message to the correct handler."""
        mtype = msg.get("type", "")
        try:
            if mtype == MSG_REGISTER:
                await self._handle_register(msg)
            elif mtype == MSG_HEARTBEAT:
                await self._handle_heartbeat(msg)
            elif mtype == MSG_ACK:
                await self._handle_ack(msg)
            elif mtype == MSG_STATE:
                await self._handle_state(msg)
            elif mtype == MSG_RESULT:
                await self._handle_result(msg)
            elif mtype == MSG_FAILED:
                await self._handle_failed(msg)
            else:
                logger.debug("[listener] Unknown message type: %s", mtype)
        except Exception as exc:
            logger.exception(
                "[listener] Handler error for type=%s job=%s: %s",
                mtype, msg.get("job_id", "?"), exc,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # REGISTER
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_register(self, msg: dict) -> None:
        missing = require_fields(msg, "worker_id", "bot_id", "capacity")
        if missing:
            logger.warning("[listener] REGISTER missing fields: %s", missing)
            return

        worker_id = msg["worker_id"]
        await self._db.upsert_worker(worker_id, {
            "bot_id":         int(msg["bot_id"]),
            "username":       msg.get("username", ""),
            "capacity":       int(msg["capacity"]),
            "version":        msg.get("version", "unknown"),
            "status":         WORKER_ONLINE,
            "active_jobs":    0,
            "last_heartbeat": time.time(),
        })
        logger.info(
            "[listener] REGISTER worker=%s bot_id=%s capacity=%s",
            worker_id, msg["bot_id"], msg["capacity"],
        )

    # ──────────────────────────────────────────────────────────────────────────
    # HEARTBEAT
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_heartbeat(self, msg: dict) -> None:
        missing = require_fields(msg, "worker_id", "active_jobs", "capacity", "status")
        if missing:
            logger.debug("[listener] HEARTBEAT missing: %s", missing)
            return

        await self._db.update_heartbeat(
            worker_id  = msg["worker_id"],
            active_jobs= int(msg["active_jobs"]),
            capacity   = int(msg["capacity"]),
            status     = msg["status"],
        )

        # Update flood_wait_until if worker reported it
        flood_until = msg.get("flood_wait_until")
        if flood_until and float(flood_until) > time.time():
            await self._db.set_worker_flood_wait(msg["worker_id"], float(flood_until))

    # ──────────────────────────────────────────────────────────────────────────
    # ACK
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_ack(self, msg: dict) -> None:
        missing = require_fields(msg, "worker_id", "job_id")
        if missing:
            logger.warning("[listener] ACK missing: %s", missing)
            return

        job_id    = msg["job_id"]
        worker_id = msg["worker_id"]

        if not await self._owns_job(job_id, worker_id):
            logger.warning("[listener] stale ACK worker=%s job=%s", worker_id, job_id)
            return

        ok = await self._db.transition_job_state(job_id, "ASSIGNED", STATE_ACKED)
        if ok:
            logger.info("[listener] ACK job=%s worker=%s", job_id, worker_id)
        else:
            # Might already be ACKED (duplicate) — not an error
            logger.debug("[listener] ACK job=%s — transition failed (duplicate?)", job_id)

    # ──────────────────────────────────────────────────────────────────────────
    # STATE
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_state(self, msg: dict) -> None:
        missing = require_fields(msg, "worker_id", "job_id", "state")
        if missing:
            logger.warning("[listener] STATE missing: %s", missing)
            return

        job_id = msg["job_id"]
        state  = msg["state"]
        if not await self._owns_job(job_id, msg["worker_id"]):
            logger.warning("[listener] stale STATE worker=%s job=%s", msg["worker_id"], job_id)
            return

        # We do an unconditional state set here — the worker is authoritative
        # for intermediate states (DOWNLOADING, PROCESSING, UPLOADING).
        await self._db.set_job_state(job_id, state)
        logger.info(
            "[listener] STATE job=%s worker=%s → %s",
            job_id, msg["worker_id"], state,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # RESULT
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_result(self, msg: dict) -> None:
        missing = require_fields(
            msg, "worker_id", "job_id",
            "output_chat_id", "output_message_id", "filename",
        )
        if missing:
            logger.warning("[listener] RESULT missing: %s", missing)
            return

        job_id    = msg["job_id"]
        worker_id = msg["worker_id"]
        filename  = msg["filename"]
        orig_name = msg.get("original_filename", filename)
        file_size = int(msg.get("file_size", 0))
        media_url = msg.get("mediainfo_url")
        out_chat  = int(msg["output_chat_id"])
        out_msg   = int(msg["output_message_id"])

        logger.info(
            "[listener] RESULT job=%s worker=%s file=%s",
            job_id, worker_id, filename,
        )

        if not await self._owns_job(job_id, worker_id):
            logger.warning("[listener] stale RESULT worker=%s job=%s", worker_id, job_id)
            return

        from shared.protocol import TERMINAL_STATES
        _j = await self._db.get_job(job_id)
        if _j and _j.get("status") in TERMINAL_STATES:
            logger.warning("[listener] RESULT job=%s already %s", job_id, _j["status"])
            return

        if _j:
            await _pace_delivery(int(_j["user_id"]))

        # Look up the job to find the destination user
        job = await self._db.get_job(job_id)
        if not job:
            logger.error("[listener] RESULT job=%s — job not found in DB", job_id)
            return

        user_id  = int(job["user_id"])
        batch_id = job.get("batch_id", "")

        # FIX BUG 5 & 6: Record delivery FIRST (atomic insert_one with unique
        # index). If it returns False the job was already delivered — release
        # the slot and bail out without calling dec_worker_active a second time.
        # This eliminates the TOCTOU window between was_delivered() check and
        # record_delivery() call, and prevents the double-decrement that
        # occurred when the old duplicate path decremented and then fell through
        # to the normal decrement.
        recorded = await self._db.record_delivery(job_id, user_id, filename)
        if not recorded:
            # Already delivered by a previous RESULT message — release slot once.
            await self._db.dec_worker_active(worker_id)
            logger.warning("[listener] RESULT job=%s dup — slot released", job_id)
            return

        # Deliver to user (server-side copy — Manager Bot never downloads).
        # record_delivery() already locked the delivery in, so a crash here
        # will NOT cause a duplicate on retry (record_delivery returns False).
        await self._deliver_to_user(
            user_id    = user_id,
            job_id     = job_id,
            out_chat   = out_chat,
            out_msg_id = out_msg,
            filename   = filename,
            job        = job,
        )

        # Dump to central dump channel
        if self._dump_ch:
            await self._dump_to_central(
                job_id    = job_id,
                user_id   = user_id,
                filename  = filename,
                orig_name = orig_name,
                file_size = file_size,
                media_url = media_url,
                out_chat  = out_chat,
                out_msg_id= out_msg,
            )

        # Terminal state transitions — release the worker slot immediately
        await self._db.dec_worker_active(worker_id)
        await self._db.set_job_state(job_id, STATE_DONE, {"finished_at": time.time()})
        await self._db.inc_batch_done(batch_id)
        await self._db.inc_worker_completed(worker_id)
        await self._db.inc_stat("total_done")
        await self._db.inc_stat(f"user_done_{user_id}")
        if file_size:
            await self._db.inc_stat("total_bytes_processed", file_size)

        # Leaderboard stats
        try:
            user_obj     = await self._bot.get_users(user_id)
            display_name = user_obj.first_name or str(user_id)
        except Exception:
            display_name = str(user_id)
        try:
            from plugins.leaderboard import record_rename
            await record_rename(user_id, display_name, filename, file_size)
        except Exception as exc:
            logger.debug("[listener] leaderboard update failed (non-fatal): %s", exc)

    async def _deliver_to_user(
        self,
        user_id:    int,
        job_id:     str,
        out_chat:   int,
        out_msg_id: int,
        filename:   str,
        job:        dict | None = None,
    ) -> None:
        """
        Forward the already-renamed file from the Worker Output Channel to
        the user.

        The Worker already uploaded the file with the correct file_name to the
        output channel (pipeline.py download+rename+upload).  The file_id in
        that message carries the new name baked in by Telegram.

        We deliberately do NOT use copy_message here: it was observed to fail
        with [400 MEDIA_EMPTY] on channel → private-user copies even though the
        exact same message copies fine channel → channel (e.g. to the central
        dump).  Instead we always fetch the source message fresh with
        self._bot (the same client that will send it) and re-send it by
        file_id via send_document/send_video/send_audio. Telegram ignores an
        explicit file_name override when sending by file_id anyway — the name
        is fixed at upload time — so we don't pass one.

        Thumbnail note: if a thumbnail_url is set we re-download it and pass
        it as `thumb` to the same send_* call — no separate code path needed
        now that we always send by file_id.
        """
        import os, tempfile, aiohttp as _aiohttp
        from html import escape as _html_escape

        caption       = f"<b>{_html_escape(filename)}</b>"
        thumbnail_url = job.get("thumbnail_url") if job else None
        thumb_path    = None

        if thumbnail_url:
            try:
                tmp_dir    = tempfile.mkdtemp()
                thumb_path = os.path.join(tmp_dir, f"thumb_{job_id}.jpg")
                async with _aiohttp.ClientSession() as session:
                    async with session.get(
                        thumbnail_url,
                        timeout=_aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        if resp.status == 200:
                            with open(thumb_path, "wb") as f:
                                f.write(await resp.read())
                        else:
                            thumb_path = None
            except Exception as exc:
                logger.warning(
                    "[listener] Thumb download failed for job=%s: %s — delivering without thumb",
                    job_id, exc,
                )
                thumb_path = None

        try:
            # IMPORTANT: always re-fetch the source message with the Manager
            # Bot's own client (self._bot), immediately before sending, and
            # send by resolved file_id rather than relying on copy_message's
            # internal file_reference.  copy_message(chat_id=<user>, caption=...)
            # was observed to fail with MEDIA_EMPTY on delivery-to-user (while
            # the identical call to a channel destination succeeded) — the
            # file_reference Telegram attaches to a channel-sourced message
            # can be rejected when Pyrogram tries to re-wrap it into a fresh
            # InputMedia for a private-chat SendMedia call. Fetching fresh via
            # the exact client that will send it avoids stale/cross-context
            # file_reference issues. We use self._bot (not self._session) so
            # the file_id is guaranteed valid for self._bot's own send_* calls.
            try:
                src_msg = await self._bot.get_messages(out_chat, out_msg_id)
            except Exception:
                src_msg = None

            if not src_msg or (
                not src_msg.document and not src_msg.video and not src_msg.audio
            ):
                raise RuntimeError(
                    f"source message {out_chat}/{out_msg_id} has no media "
                    f"(fetch {'failed' if not src_msg else 'returned no media'})"
                )

            send_kwargs = dict(caption=caption)
            if thumb_path:
                send_kwargs["thumb"] = thumb_path

            if src_msg.document:
                await self._bot.send_document(
                    chat_id  = user_id,
                    document = src_msg.document.file_id,
                    **send_kwargs,
                )
            elif src_msg.video:
                await self._bot.send_video(
                    chat_id  = user_id,
                    video    = src_msg.video.file_id,
                    duration = src_msg.video.duration,
                    width    = src_msg.video.width,
                    height   = src_msg.video.height,
                    **send_kwargs,
                )
            else:  # src_msg.audio
                await self._bot.send_audio(
                    chat_id  = user_id,
                    audio    = src_msg.audio.file_id,
                    duration = src_msg.audio.duration,
                    **send_kwargs,
                )

            logger.info(
                "[listener] Delivered job=%s to user=%s file=%s (thumb=%s)",
                job_id, user_id, filename, bool(thumb_path),
            )
        except Exception as exc:
            logger.error(
                "[listener] Delivery FAILED job=%s user=%s: %s",
                job_id, user_id, exc,
            )
            await self._db.set_job_state(job_id, STATE_FAILED, {
                "fail_reason": f"delivery_failed: {exc}",
            })
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                    os.rmdir(os.path.dirname(thumb_path))
                except OSError:
                    pass

    async def _dump_to_central(
        self,
        job_id:    str,
        user_id:   int,
        filename:  str,
        orig_name: str,
        file_size: int,
        media_url: str | None,
        out_chat:  int,
        out_msg_id: int,
    ) -> None:
        """Copy completed file to the central dump channel with rich caption (§26)."""
        from helper.utils import humanbytes

        # Fetch username for the dump caption
        try:
            user = await self._bot.get_users(user_id)
            username = f"@{user.username}" if user.username else str(user_id)
        except Exception:
            username = str(user_id)

        size_str = humanbytes(file_size) if file_size else "?"
        mi_line  = f"\n🎞 MediaInfo: {media_url}" if media_url else ""

        caption = (
            f"╭━━━〔 📂 FILE INFO 〕━━━╮\n\n"
            f"📂 Original:\n{orig_name}\n\n"
            f"➜ ✏️ Renamed:\n{filename}\n\n"
            f"👤 User: {username}\n"
            f"🆔 ID: {user_id}\n\n"
            f"🆔 Job: {job_id}\n"
            f"📦 Size: {size_str}\n"
            f"📊 Status: Completed"
            f"{mi_line}\n\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━╯"
        )

        try:
            # copy_message from output channel → central dump.
            # The output channel message already has the correct file_name
            # from the worker's upload, so no get_messages/send_document needed.
            await self._bot.copy_message(
                chat_id      = self._dump_ch,
                from_chat_id = out_chat,
                message_id   = out_msg_id,
                caption      = caption,
            )
            logger.info("[listener] Dumped job=%s to central dump", job_id)
        except Exception as exc:
            logger.error("[listener] Central dump FAILED job=%s: %s", job_id, exc)

    # ──────────────────────────────────────────────────────────────────────────
    # FAILED
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_failed(self, msg: dict) -> None:
        missing = require_fields(msg, "worker_id", "job_id", "reason")
        if missing:
            logger.warning("[listener] FAILED missing: %s", missing)
            return

        job_id    = msg["job_id"]
        worker_id = msg["worker_id"]
        reason    = msg["reason"]

        logger.warning(
            "[listener] FAILED job=%s worker=%s reason=%s",
            job_id, worker_id, reason,
        )

        if not await self._owns_job(job_id, worker_id):
            logger.warning("[listener] stale FAILED worker=%s job=%s", worker_id, job_id)
            return

        from shared.protocol import TERMINAL_STATES
        job = await self._db.get_job(job_id)
        if not job or job.get("status") in TERMINAL_STATES:
            return

        user_id  = int(job["user_id"])
        batch_id = job.get("batch_id", "")

        # Release the worker slot immediately so the scheduler can fill it
        await self._db.dec_worker_active(worker_id)

        await self._db.set_job_state(job_id, STATE_FAILED, {
            "fail_reason":  reason,
            "finished_at":  time.time(),
        })
        await self._db.inc_batch_failed(batch_id)
        await self._db.inc_worker_failed(worker_id)
        await self._db.inc_stat("total_failed")

        # Notify user of the failure
        try:
            await self._bot.send_message(
                user_id,
                f"╭━━━〔 ❌ JOB FAILED 〕━━━╮\n"
                f"┃  🆔  <code>{job_id}</code>\n"
                f"┃  ⚠️  {reason[:200]}\n"
                f"╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"<i>Please re-send the file to retry.</i>",
            )
        except Exception as exc:
            logger.debug("[listener] Could not notify user=%s of failure: %s", user_id, exc)
