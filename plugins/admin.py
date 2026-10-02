"""
manager/plugins/admin.py
══════════════════════════════════════════════════════════════════════════════
Admin-only commands: ban, unban, broadcast, ping, restart, setlimit.
Adapted from original — jishubotz → get_db(), Config.ADMIN → Config.OWNER_IDS,
no helper.ui dependency.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from config import Config
from helper.database import get_db

logger = logging.getLogger(__name__)


# ── /ping ─────────────────────────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command("ping"))
async def cmd_ping(client: Client, message: Message) -> None:
    start = time.time()
    rm    = await message.reply_text("🏓 Pinging…")
    ms    = (time.time() - start) * 1000
    await rm.edit_text(
        f"╭━━━〔 🏓 PONG 〕━━━╮\n"
        f"┃  ⚡  {ms:.1f} ms\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ── /restart ──────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("restart") & filters.user(Config.OWNER_IDS))
async def cmd_restart(client: Client, message: Message) -> None:
    await message.reply_text(
        "╭━━━〔 🔄 RESTARTING 〕━━━╮\n"
        "┃  🌀  Restarting process…\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    await asyncio.sleep(1)
    os.execl(sys.executable, sys.executable, *sys.argv)


# ── /ban ──────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("ban") & filters.user(Config.OWNER_IDS))
async def cmd_ban(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.split(None, 2)
    if len(parts) < 2:
        return await message.reply_text("Usage: /ban [user_id] [reason]")

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    reason = parts[2] if len(parts) > 2 else "No reason provided"

    already = await db.is_banned(target_id)
    if already:
        return await message.reply_text(
            f"╭━━━〔 🛡 ALREADY BANNED 〕━━━╮\n"
            f"┃  <code>{target_id}</code> is already restricted.\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    await db.ban_user(target_id)
    await message.reply_text(
        f"╭━━━〔 🛡 BANNED 〕━━━╮\n"
        f"┃  🆔  <code>{target_id}</code>\n"
        f"┃  ⚠️  {reason}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📨 Notify User", callback_data=f"ban_notify_{target_id}"),
            InlineKeyboardButton("🤫 Silent",       callback_data="ban_silent"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^ban_notify_(\d+)$"))
async def cb_ban_notify(client: Client, query: CallbackQuery) -> None:
    target_id = int(query.data.split("_")[2])
    try:
        await client.send_message(
            target_id,
            "╭━━━〔 ⛔ ACCESS DENIED 〕━━━╮\n"
            "┃  You have been banned from this bot.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await query.answer("Notification sent ✅")
    except Exception:
        await query.answer("Could not DM user ⚠️")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^ban_silent$"))
async def cb_ban_silent(client: Client, query: CallbackQuery) -> None:
    await query.answer("Silent — no notification sent.")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── /unban ────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("unban") & filters.user(Config.OWNER_IDS))
async def cmd_unban(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text("Usage: /unban [user_id]")

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    is_banned = await db.is_banned(target_id)
    if not is_banned:
        return await message.reply_text(
            f"╭━━━〔 🛡 NOT BANNED 〕━━━╮\n"
            f"┃  <code>{target_id}</code> has no restriction.\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    await db.unban_user(target_id)
    await message.reply_text(
        f"╭━━━〔 ✅ UNBANNED 〕━━━╮\n"
        f"┃  <code>{target_id}</code> access restored.\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📨 Notify User", callback_data=f"unban_notify_{target_id}"),
            InlineKeyboardButton("🤫 Silent",       callback_data="ban_silent"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^unban_notify_(\d+)$"))
async def cb_unban_notify(client: Client, query: CallbackQuery) -> None:
    target_id = int(query.data.split("_")[2])
    try:
        await client.send_message(
            target_id,
            "╭━━━〔 ✅ ACCESS RESTORED 〕━━━╮\n"
            "┃  Your access has been restored.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        await query.answer("Notification sent ✅")
    except Exception:
        await query.answer("Could not DM user ⚠️")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── /setlimit ─────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("setlimit") & filters.user(Config.OWNER_IDS))
async def cmd_setlimit(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.strip().split()
    if len(parts) < 2:
        current = await db.get_auto_daily_limit()
        return await message.reply_text(
            f"╭━━━〔 ⚡ DAILY LIMIT 〕━━━╮\n"
            f"┃  Current: {current} jobs/day\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Usage: <code>/setlimit [number]</code>"
        )
    try:
        n = int(parts[1])
        if n < 0:
            raise ValueError
    except ValueError:
        return await message.reply_text("❌ Must be a non-negative integer.")

    await db.set_auto_daily_limit(n)
    await message.reply_text(
        f"╭━━━〔 ⚡ DAILY LIMIT SET 〕━━━╮\n"
        f"┃  {n} jobs/day for free users\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ── /broadcast ────────────────────────────────────────────────────────────────

@Client.on_message(
    filters.command("broadcast") & filters.user(Config.OWNER_IDS) & filters.reply
)
async def cmd_broadcast(client: Client, message: Message) -> None:
    db            = get_db()
    broadcast_msg = message.reply_to_message
    sts           = await message.reply_text(
        "╭━━━〔 📢 BROADCAST 〕━━━╮\n┃  🌀  Starting…\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )

    cursor = db.users.find({}, {"_id": 1})
    users  = await cursor.to_list(length=100_000)

    done = success = failed = 0
    sem  = asyncio.Semaphore(20)
    start_time = time.time()

    async def _send_one(user_id: int) -> None:
        nonlocal done, success, failed
        async with sem:
            try:
                await broadcast_msg.copy(chat_id=user_id)
                success += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                try:
                    await broadcast_msg.copy(chat_id=user_id)
                    success += 1
                except Exception:
                    failed += 1
            except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
                failed += 1
            except Exception as exc:
                logger.debug("[broadcast] %s: %s", user_id, exc)
                failed += 1
        done += 1
        if done % 25 == 0:
            pct = int(done / len(users) * 10) if users else 0
            bar = "█" * pct + "░" * (10 - pct)
            try:
                await sts.edit_text(
                    f"╭━━━〔 📢 BROADCASTING 〕━━━╮\n"
                    f"┃  [{bar}]  {done}/{len(users)}\n"
                    f"┃  ✅  {success}  ❌  {failed}\n"
                    f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
                )
            except Exception:
                pass

    await asyncio.gather(*[_send_one(u["_id"]) for u in users], return_exceptions=True)

    elapsed = int(time.time() - start_time)
    await sts.edit_text(
        f"╭━━━〔 📢 BROADCAST DONE 〕━━━╮\n"
        f"┃  ⏱  {elapsed}s\n"
        f"┃  👥  {len(users)}\n"
        f"┃  ✅  {success}  ❌  {failed}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
