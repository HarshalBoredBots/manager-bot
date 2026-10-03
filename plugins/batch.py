"""
manager/plugins/batch.py
══════════════════════════════════════════════════════════════════════════════
Batch workflow commands — the primary user-facing flow.

Commands
────────
  /autorename <pattern>  — Open a batch.  Every file the user sends after
                           this is queued for renaming with the given pattern.
                           The pattern is a format template identical to the
                           original bot's /autorename command:
                             e.g.  Naruto S{season}E{episode} [{quality}]
                           Template rendering (episode/season/quality
                           extraction) happens HERE in the Manager, using
                           the same regex engine from the original
                           auto_rename_engine.py.

  /done                  — Close the current batch.  No more files accepted.
                           Shows a summary of queued/active/done counts.

  /cancelall             — Cancel all QUEUED jobs in the current batch.
                           Already-assigned jobs run to completion.

  /queuestatus           — Live batch + job status panel.

  /clearqueue (admin)    — Wipe all QUEUED jobs across all users.

File handler             — Handles documents/video/audio sent after /autorename.

Design notes
────────────
  • Batch is per-user; only one OPEN batch at a time.
  • Files sent without an open batch → prompt to use /autorename first.
  • Template rendering is done Manager-side before creating the job document,
    so Workers never need the template engine.  They receive a fully-resolved
    rename_pattern string.
  • Pipeline settings (prefix, suffix, metadata, thumbnail_url, dump_enabled)
    are snapshotted into the job document at creation time — a user settings
    change mid-batch does NOT affect already-queued jobs.
  • ImgBB upload of thumbnail happens here (if IMGBB_API_KEY is set) so all
    Workers share the same HTTPS URL regardless of deployment.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from typing import Optional

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from helper.database import get_db
from helper.utils import humanbytes

logger = logging.getLogger(__name__)

# ── Episode/season parser — pure functions, no Pyrogram/DB imports ─────────────
from plugins.template_engine import _apply_template


# ══════════════════════════════════════════════════════════════════════════════
# /autorename  — open a batch
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("autorename"))
async def cmd_autorename(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id

    if await db.is_banned(user_id):
        return await message.reply_text("⛔ <b>Access Denied</b>")

    is_prem = await db.is_premium(user_id, Config.OWNER_IDS)
    if not is_prem:
        return await message.reply_text(
            "💎 <b>Premium Required</b>\n\n"
            "Auto-rename batches require a premium plan.\n"
            "Contact the admin to upgrade."
        )

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        current = await db.get_format_template(user_id)
        tpl_line = f"<code>{current}</code>" if current else "<i>None set yet</i>"
        return await message.reply_text(
            "◈ <b>Auto Rename — Batch Mode</b>\n\n"
            f"Current template: {tpl_line}\n\n"
            "Usage:\n"
            "<code>/autorename Naruto S{season}E{episode} [{quality}]</code>\n\n"
            "Placeholders: <code>{episode}</code> <code>{season}</code> "
            "<code>{quality}</code> <code>{audio}</code>\n\n"
            "After setting a template, send files one by one.\n"
            "Type /done when all files are sent."
        )

    template = parts[1].strip()
    await db.set_format_template(user_id, template)

    # Close any existing open batch before opening a new one
    existing = await db.get_active_batch(user_id)
    if existing:
        await db.close_batch(existing["batch_id"], user_id)
        logger.info(
            "[batch] Closed previous batch=%s for user=%s",
            existing["batch_id"], user_id,
        )

    batch_id = f"b_{user_id}_{uuid.uuid4().hex[:8]}"
    await db.create_batch(batch_id, user_id, template)

    logger.info("[batch] Opened batch=%s user=%s template=%r", batch_id, user_id, template)

    await message.reply_text(
        "╭━━━〔 🌌 BATCH OPENED 〕━━━╮\n"
        f"┃  📋  <code>{template}</code>\n"
        "┃\n"
        "┃  Send your files one by one.\n"
        "┃  Type /done when finished.\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "<i>⚡ Manager will queue each file to the fastest available worker.</i>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done", callback_data="batch_done"),
            InlineKeyboardButton("❌ Cancel All", callback_data="batch_cancelall"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# File handler  — queues files sent while a batch is open
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(
    filters.private
    & (filters.document | filters.video | filters.audio)
)
async def handle_file(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id

    if await db.is_banned(user_id):
        return

    batch = await db.get_active_batch(user_id)
    if not batch:
        # No open batch — fall through to manual rename plugin or prompt
        return await message.reply_text(
            "╭━━━〔 ℹ️ NO ACTIVE BATCH 〕━━━╮\n"
            "┃  Send /autorename <template> first\n"
            "┃  to start a rename batch.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    batch_id = batch["batch_id"]
    template = batch["rename_pattern"]

    # ── Identify media ────────────────────────────────────────────────────────
    if message.document:
        file_obj  = message.document
        base_name = file_obj.file_name or "document"
    elif message.video:
        file_obj  = message.video
        base_name = file_obj.file_name or "video.mp4"
    elif message.audio:
        file_obj  = message.audio
        base_name = file_obj.file_name or "audio.mp3"
    else:
        return

    file_size = getattr(file_obj, "file_size", 0) or 0
    caption   = (message.caption or "").strip()

    # Size check — workers are standard bots (2 GB limit)

    # ── Snapshot pipeline settings ────────────────────────────────────────────
    ps = await db.get_pipeline_settings(user_id, Config.OWNER_IDS)

    # ── Resolve upload_as from media preference ───────────────────────────────
    _media_pref = ps.get("auto_media_type")   # None | "document" | "video" | "audio"
    if _media_pref == "auto" or not _media_pref:
        # Infer from Telegram message type
        if message.video:
            _upload_as = "video"
        elif message.audio:
            _upload_as = "audio"
        else:
            _upload_as = "document"
    else:
        _upload_as = _media_pref   # explicit user choice

    # ── Resolve thumbnail URL ─────────────────────────────────────────────────
    thumbnail_url = ps.get("thumbnail_url")

    # ── Apply template → resolve rename pattern ───────────────────────────────
    rename_source = ps.get("rename_source", "filename")
    if rename_source == "caption":
        extraction_text = caption if caption else base_name
    elif rename_source == "both":
        extraction_text = (caption + " " + base_name).strip() if caption else base_name
    else:
        extraction_text = base_name

    # Strip CRC32 hash (same as original)
    extraction_text = re.sub(r'\[[0-9A-Fa-f]{8}\]', '', extraction_text).strip()

    rename_pattern = _apply_template(template, extraction_text, base_name)

    # ── Daily limit check ─────────────────────────────────────────────────────
    if not ps.get("premium") and not ps.get("is_admin"):
        count = await db.get_auto_daily_count(user_id)
        limit = await db.get_auto_daily_limit()
        if count >= limit:
            return await message.reply_text(
                "╭━━━〔 ⚡ DAILY LIMIT REACHED 〕━━━╮\n"
                f"┃  📊  {count}/{limit} jobs used today\n"
                "┃  ⏱   Resets at midnight UTC\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                "Upgrade to premium for unlimited access. 👑"
            )

    # ── Copy file to Worker Output Channel so all worker bots can access it ─────
    # The user's DM with the Manager bot is private — worker bots cannot call
    # get_messages() on it.  We copy the file to the shared Worker Output
    # Channel (where all worker bots are admins) and store those coordinates.
    #
    # FIX BUG 7: Generate job_id ONCE here and reuse it for both the staging
    # call (for log correlation) and the job document.  Previously _make_job_id()
    # was called twice — once in the staging call and once below — producing two
    # different UUIDs.  The staging log would say job_id=X while the DB stored
    # job_id=Y, making debugging impossible and the logs misleading.
    from config import Config as _Config
    from helper.staging import stage_to_output_channel

    job_id = _make_job_id(user_id)   # single call — reused below

    try:
        _copied = await stage_to_output_channel(
            message, _Config.WORKER_OUTPUT_CHANNEL_ID, job_id=job_id
        )
        _src_chat_id = _copied.chat.id
        _src_msg_id  = _copied.id
    except Exception as _exc:
        logger.error("[batch] staging failed user=%s: %s", user_id, _exc)
        return await message.reply_text(
            "╭━━━〔 ❌ STAGING FAILED 〕━━━╮\n"
            "┃  Telegram rate-limited the bot.\n"
            "┃  Retry in 1-2 minutes.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    # ── Build job document ────────────────────────────────────────────────────
    metadata_version = 1

    # Hardcoded metadata — always embed @Animes_Ocean.
    # Cannot be changed by any user, command, or database setting.
    metadata = {
        "title":    "@Animes_Ocean",
        "artist":   "@Animes_Ocean",
        "author":   "@Animes_Ocean",
        "comment":  "@Animes_Ocean",
        "audio":    "@Animes_Ocean",
        "video":    "@Animes_Ocean",
        "subtitle": "@Animes_Ocean",
    }

    job = {
        "job_id":             job_id,      # reuse the same ID generated above
        "batch_id":           batch_id,
        "user_id":            user_id,
        "source_chat_id":     _src_chat_id,
        "source_message_id":  _src_msg_id,
        "rename_pattern":     rename_pattern,
        "prefix":             ps.get("prefix", ""),
        "suffix":             ps.get("suffix", ""),
        "metadata":           metadata,
        "metadata_version":   metadata_version,
        "thumbnail_url":      thumbnail_url,
        "dump_enabled":       bool(ps.get("dump_mode") and ps.get("dump_channel")),
        "upload_as":          _upload_as,        # NEW — "document" | "video" | "audio"
        "original_filename":  base_name,
        "file_size":          file_size,
        "created_at":         time.time(),
    }

    await db.create_job(job)
    await db.inc_batch_file_count(batch_id)

    if not ps.get("premium") and not ps.get("is_admin"):
        await db.inc_auto_daily(user_id)

    # ── Ack to user ───────────────────────────────────────────────────────────
    queue_count = batch.get("file_count", 0) + 1
    await message.reply_text(
        f"╭━━━〔 ✅ QUEUED 〕━━━╮\n"
        f"┃  🆔  <code>{job_id}</code>\n"
        f"┃  📂  <code>{rename_pattern[:40]}</code>\n"
        f"┃  📦  {humanbytes(file_size)}\n"
        f"┃  🔢  File #{queue_count} in batch\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "<i>⚡ Will be dispatched to a worker automatically.</i>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Status", callback_data="batch_status"),
            InlineKeyboardButton("❌ Cancel All", callback_data="batch_cancelall"),
        ]]),
    )

    logger.info(
        "[batch] Queued job=%s batch=%s user=%s file=%s",
        job_id, batch_id, user_id, rename_pattern,
    )


def _make_job_id(user_id: int) -> str:
    """Generate a unique, human-readable job ID."""
    return f"j{user_id % 10000:04d}_{uuid.uuid4().hex[:6]}"


# ══════════════════════════════════════════════════════════════════════════════
# /done  — close the batch
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("done"))
async def cmd_done(client: Client, message: Message) -> None:
    await _close_batch(client, message.from_user.id, message)


@Client.on_callback_query(filters.regex(r"^batch_done$"))
async def cb_done(client: Client, update: CallbackQuery) -> None:
    await update.answer()
    await _close_batch(client, update.from_user.id, update.message)


async def _close_batch(client: Client, user_id: int, reply_target) -> None:
    db    = get_db()
    batch = await db.get_active_batch(user_id)
    if not batch:
        return await reply_target.reply_text(
            "╭━━━〔 ℹ️ NO ACTIVE BATCH 〕━━━╮\n"
            "┃  No open batch to close.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    batch_id   = batch["batch_id"]
    closed     = await db.close_batch(batch_id, user_id)

    if not closed:
        return await reply_target.reply_text("Batch already closed.")

    file_count = batch.get("file_count", 0)

    await reply_target.reply_text(
        "╭━━━〔 🔒 BATCH CLOSED 〕━━━╮\n"
        f"┃  🆔  <code>{batch_id}</code>\n"
        f"┃  📁  {file_count} file(s) queued\n"
        "┃\n"
        "┃  Workers are processing your files.\n"
        "┃  You will receive each result as it\n"
        "┃  completes — no need to wait here.\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "<i>Use /queuestatus to check progress.</i>",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Status",       callback_data="batch_status"),
            InlineKeyboardButton("🗑 Cancel Batch", callback_data=f"cancel_batch:{batch_id}"),
        ]]),
    )

    logger.info("[batch] Closed batch=%s user=%s files=%d", batch_id, user_id, file_count)


# ══════════════════════════════════════════════════════════════════════════════
# /cancelall  — cancel queued jobs in current batch
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("cancelall"))
async def cmd_cancelall(client: Client, message: Message) -> None:
    await _cancelall(client, message.from_user.id, message)


@Client.on_callback_query(filters.regex(r"^batch_cancelall$"))
async def cb_cancelall(client: Client, update: CallbackQuery) -> None:
    await update.answer()
    await _cancelall(client, update.from_user.id, update.message)


async def _cancelall(client: Client, user_id: int, reply_target) -> None:
    db    = get_db()
    batch = await db.get_active_batch(user_id)
    if not batch:
        return await reply_target.reply_text("No active batch.")

    batch_id = batch["batch_id"]

    # Cancel only QUEUED jobs — assigned/running jobs complete normally
    result = await db.jobs.update_many(
        {"batch_id": batch_id, "status": "QUEUED"},
        {"$set": {"status": "FAILED", "fail_reason": "Cancelled by user"}},
    )
    cancelled = result.modified_count

    await reply_target.reply_text(
        "╭━━━〔 🗑 CANCEL ALL 〕━━━╮\n"
        f"┃  Cancelled {cancelled} queued job(s).\n"
        "┃  In-flight jobs complete normally.\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    logger.info(
        "[batch] CancelAll batch=%s user=%s cancelled=%d",
        batch_id, user_id, cancelled,
    )


# ══════════════════════════════════════════════════════════════════════════════
# cancel_batch:<batch_id>  — cancel a specific batch by ID (inline button)
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^cancel_batch:(.+)$"))
async def cb_cancel_batch_by_id(client: Client, update: CallbackQuery) -> None:
    """Cancel all QUEUED jobs in a specific batch by its ID.
    Works even after the batch is closed — the batch_id comes from the button."""
    await update.answer()

    user_id  = update.from_user.id
    batch_id = update.matches[0].group(1)

    db  = get_db()

    # Verify the batch belongs to this user
    batch = await db.get_batch(batch_id)
    if not batch:
        return await update.message.edit_text(
            "╭━━━〔 ⚠️ NOT FOUND 〕━━━╮\n"
            f"┃  Batch <code>{batch_id}</code> not found.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    if int(batch.get("user_id", -1)) != user_id:
        return await update.answer("⛔ This is not your batch.", show_alert=True)

    # Cancel only QUEUED jobs — assigned/running jobs complete normally
    result = await db.jobs.update_many(
        {"batch_id": batch_id, "status": "QUEUED"},
        {"$set": {"status": "FAILED", "fail_reason": "Cancelled by user"}},
    )
    cancelled = result.modified_count

    await update.message.edit_text(
        "╭━━━〔 🗑 BATCH CANCELLED 〕━━━╮\n"
        f"┃  🆔  <code>{batch_id}</code>\n"
        f"┃  Cancelled {cancelled} queued job(s).\n"
        "┃  In-flight jobs complete normally.\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    logger.info(
        "[batch] CancelByID batch=%s user=%s cancelled=%d",
        batch_id, user_id, cancelled,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /queuestatus  — live status panel
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("queuestatus"))
async def cmd_queuestatus(client: Client, message: Message) -> None:
    await _send_status(client, message.from_user.id, message)


@Client.on_callback_query(filters.regex(r"^batch_status$"))
async def cb_queuestatus(client: Client, update: CallbackQuery) -> None:
    await update.answer()
    await _send_status(client, update.from_user.id, update.message)


async def _send_status(client: Client, user_id: int, reply_target) -> None:
    db = get_db()

    # Worker pool stats
    all_workers = await db.get_all_workers()
    online  = [w for w in all_workers if w.get("status") in ("ONLINE", "BUSY")]
    busy    = [w for w in all_workers if w.get("status") == "BUSY"]
    offline = [w for w in all_workers if w.get("status") == "OFFLINE"]

    # User's active jobs
    active_jobs = await db.get_user_active_jobs(user_id)
    by_state: dict[str, int] = {}
    for j in active_jobs:
        s = j.get("status", "?")
        by_state[s] = by_state.get(s, 0) + 1

    total_queued  = by_state.get("QUEUED", 0)
    total_running = sum(
        by_state.get(s, 0)
        for s in ("ASSIGNED", "ACKED", "DOWNLOADING", "PROCESSING", "UPLOADING", "UPLOADED")
    )

    def _w(w):
        return f"@{w.get('username', '')} ({w.get('active_jobs',0)}/{w.get('capacity',1)})"

    worker_lines = ""
    if online:
        worker_lines = "\n".join(f"┃  🟢  {_w(w)}" for w in online[:5])
    if offline:
        worker_lines += f"\n┃  🔴  {len(offline)} offline"

    batch = await db.get_active_batch(user_id)
    batch_line = (
        f"┃  📦  Batch: <code>{batch['batch_id'][-12:]}</code>  ({batch['file_count']} files)\n"
        if batch else ""
    )

    text = (
        "╭━━━〔 📊 QUEUE STATUS 〕━━━╮\n"
        f"{batch_line}"
        f"┃  ⏳  Queued    ·  {total_queued}\n"
        f"┃  ⚙️  Running   ·  {total_running}\n"
        "┃\n"
        "┃  🤖  Workers:\n"
        f"{worker_lines or '┃  (none registered)'}\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )

    await reply_target.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="batch_status"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# /clearqueue  (admin only)
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("clearqueue"))
async def cmd_clearqueue(client: Client, message: Message) -> None:
    if message.from_user.id not in Config.OWNER_IDS:
        return await message.reply_text("⛔ <b>Admin only.</b>")

    db = get_db()
    result = await db.jobs.update_many(
        {"status": "QUEUED"},
        {"$set": {"status": "FAILED", "fail_reason": "Admin clearqueue"}},
    )
    await message.reply_text(
        "╭━━━〔 🗑 QUEUE CLEARED 〕━━━╮\n"
        f"┃  Cancelled {result.modified_count} queued job(s).\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
