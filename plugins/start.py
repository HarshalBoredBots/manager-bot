"""
manager/plugins/start.py
══════════════════════════════════════════════════════════════════════════════
/start, /help, /status commands for the Manager bot.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from helper.database import get_db


# ══════════════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("start"))
async def cmd_start(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id

    if await db.is_banned(user_id):
        return await message.reply_text("⛔ <b>You are banned from using this bot.</b>")

    me      = await client.get_me()
    mention = message.from_user.mention

    await message.reply_text(
        f"╭━━━〔 🌌 WELCOME 〕━━━╮\n"
        f"┃\n"
        f"┃  Hello, {mention}!\n"
        f"┃  I am <b>{me.first_name}</b> — the\n"
        f"┃  Distributed Rename Manager.\n"
        f"┃\n"
        f"┃  I coordinate a fleet of Worker\n"
        f"┃  bots to rename your files fast,\n"
        f"┃  in parallel, at any scale.\n"
        f"┃\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"➤  /autorename  ·  start a rename batch\n"
        f"➤  /done        ·  close the batch\n"
        f"➤  /help        ·  all commands\n"
        f"➤  /status      ·  worker pool status",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Help",          callback_data="help_main"),
            InlineKeyboardButton("📊 Worker Status", callback_data="batch_status"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# /help
# ══════════════════════════════════════════════════════════════════════════════

_HELP_TEXT = (
    "╭━━━〔 📖 HELP 〕━━━╮\n"
    "┃\n"
    "┃  <b>Batch Rename Flow</b>\n"
    "┃  ─────────────────────\n"
    "┃  1️⃣  /autorename <template>\n"
    "┃      Opens a batch.  Template\n"
    "┃      placeholders:\n"
    "┃      {episode}  {season}\n"
    "┃      {quality}  {audio}\n"
    "┃\n"
    "┃  2️⃣  Send your files one by one\n"
    "┃      Each is queued instantly.\n"
    "┃\n"
    "┃  3️⃣  /done  ·  close the batch\n"
    "┃      Workers process in parallel.\n"
    "┃      Results arrive as they finish.\n"
    "┃\n"
    "┃  <b>Other Commands</b>\n"
    "┃  ─────────────────────\n"
    "┃  /queuestatus  ·  live progress\n"
    "┃  /cancelall   ·  cancel queued\n"
    "┃  /view_thumb  ·  your thumbnail\n"
    "┃  /del_thumb   ·  delete thumbnail\n"
    "┃  /status      ·  worker pool\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
)


@Client.on_message(filters.private & filters.command("help"))
async def cmd_help(client: Client, message: Message) -> None:
    await message.reply_text(
        _HELP_TEXT,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="help_main"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^help_main$"))
async def cb_help(client: Client, update: CallbackQuery) -> None:
    await update.answer()
    try:
        await update.message.edit_text(
            _HELP_TEXT,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="help_main"),
            ]]),
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# /status  —  worker pool overview (alias for /queuestatus without batch info)
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("status"))
async def cmd_status(client: Client, message: Message) -> None:
    db = get_db()

    workers    = await db.get_all_workers()
    online     = [w for w in workers if w.get("status") in ("ONLINE", "BUSY")]
    offline    = [w for w in workers if w.get("status") == "OFFLINE"]
    total_cap  = sum(w.get("capacity", 1) for w in online)
    total_busy = sum(w.get("active_jobs", 0) for w in online)

    uptime_sec = int(time.time() - Config.BOT_UPTIME)
    h, rem     = divmod(uptime_sec, 3600)
    m, s       = divmod(rem, 60)
    uptime_str = f"{h}h {m}m {s}s"

    total_done   = await db.get_stat("total_done")
    total_failed = await db.get_stat("total_failed")

    worker_lines = ""
    for w in online[:8]:
        icon = "🟢" if w.get("status") == "ONLINE" else "🔵"
        aj   = w.get("active_jobs", 0)
        cap  = w.get("capacity", 1)
        bar  = "█" * aj + "░" * (cap - aj)
        worker_lines += (
            f"┃  {icon}  {w.get('worker_id','?')}\n"
            f"┃      [{bar}] {aj}/{cap}\n"
        )
    if offline:
        worker_lines += f"┃  🔴  {len(offline)} offline\n"
    if not workers:
        worker_lines = "┃  ⚠️  No workers registered yet\n"

    await message.reply_text(
        "╭━━━〔 📊 SYSTEM STATUS 〕━━━╮\n"
        f"┃  ⏱️  Uptime     ·  {uptime_str}\n"
        f"┃  ✅  Done       ·  {total_done}\n"
        f"┃  ❌  Failed     ·  {total_failed}\n"
        "┃\n"
        f"┃  🤖  Workers   ·  {len(online)} online / {len(offline)} offline\n"
        f"┃  ⚡  Load      ·  {total_busy}/{total_cap} slots\n"
        "┃\n"
        f"{worker_lines}"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^status_refresh$"))
async def cb_status_refresh(client: Client, update: CallbackQuery) -> None:
    await update.answer("Refreshed ✅")
    # Re-use cmd_status logic
    db = get_db()

    workers    = await db.get_all_workers()
    online     = [w for w in workers if w.get("status") in ("ONLINE", "BUSY")]
    offline    = [w for w in workers if w.get("status") == "OFFLINE"]
    total_cap  = sum(w.get("capacity", 1) for w in online)
    total_busy = sum(w.get("active_jobs", 0) for w in online)

    uptime_sec = int(time.time() - Config.BOT_UPTIME)
    h, rem     = divmod(uptime_sec, 3600)
    m, s       = divmod(rem, 60)
    uptime_str = f"{h}h {m}m {s}s"

    total_done   = await db.get_stat("total_done")
    total_failed = await db.get_stat("total_failed")

    worker_lines = ""
    for w in online[:8]:
        icon = "🟢" if w.get("status") == "ONLINE" else "🔵"
        aj   = w.get("active_jobs", 0)
        cap  = w.get("capacity", 1)
        bar  = "█" * aj + "░" * (cap - aj)
        worker_lines += (
            f"┃  {icon}  {w.get('worker_id','?')}\n"
            f"┃      [{bar}] {aj}/{cap}\n"
        )
    if offline:
        worker_lines += f"┃  🔴  {len(offline)} offline\n"
    if not workers:
        worker_lines = "┃  ⚠️  No workers registered yet\n"

    try:
        await update.message.edit_text(
            "╭━━━〔 📊 SYSTEM STATUS 〕━━━╮\n"
            f"┃  ⏱️  Uptime     ·  {uptime_str}\n"
            f"┃  ✅  Done       ·  {total_done}\n"
            f"┃  ❌  Failed     ·  {total_failed}\n"
            "┃\n"
            f"┃  🤖  Workers   ·  {len(online)} online / {len(offline)} offline\n"
            f"┃  ⚡  Load      ·  {total_busy}/{total_cap} slots\n"
            "┃\n"
            f"{worker_lines}"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
            ]]),
        )
    except Exception:
        pass
