"""
manager/plugins/premium.py
══════════════════════════════════════════════════════════════════════════════
Premium user system.
Adapted from original — jishubotz → get_db(), Config.ADMIN → Config.OWNER_IDS.
get_all_premium_users uses a direct MongoDB cursor, not async generator wrapper.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from helper.database import get_db

_FREE_FEATURES = (
    "Batch auto-rename",
    "Custom caption",
    "Prefix / suffix tags",
    "Custom thumbnail",
    "Metadata injection",
)

_PREMIUM_FEATURES = (
    "Everything in Free  +",
    "Priority processing",
    "Dump channel support",
    "Unlimited daily jobs",
    "Early access to new features",
)


def _feature_block(lines: tuple, tick: str = "✅") -> str:
    return "\n".join(f"{tick}  {line}" for line in lines)


# ── /premium ──────────────────────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command("premium"))
async def cmd_premium(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id
    mention = message.from_user.mention
    now     = time.time()

    if user_id in Config.OWNER_IDS:
        total_done = await db.get_stat("total_done")
        return await message.reply_text(
            f"╭━━━〔 👑 ADMIN COUNCIL 〕━━━╮\n"
            f"┃  🌌  {mention}\n"
            f"┃  🛡   Role     ·  Admin\n"
            f"┃  ⚡  Access  ·  Lifetime ∞\n"
            f"┃  📊  Bot Done  ·  {total_done:,}\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "✨ <b>All features unlocked.</b>"
        )

    is_prem = await db.is_premium(user_id)
    if is_prem:
        doc    = await db.get_user(user_id)
        expiry = doc.get("premium_expiry")
        if expiry:
            dt        = datetime.fromtimestamp(expiry, tz=timezone.utc)
            days_left = max(0, int((expiry - now) / 86400))
            plan_str  = f"{dt.strftime('%d %b %Y')}  ({days_left}d left)"
        else:
            plan_str = "Lifetime ∞"
        await message.reply_text(
            f"╭━━━〔 👑 PREMIUM ACTIVE 〕━━━╮\n"
            f"┃  🌌  {mention}\n"
            f"┃  ⭐  Status    ·  Active\n"
            f"┃  📅  Expires   ·  {plan_str}\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"<b>✨ Features:</b>\n{_feature_block(_PREMIUM_FEATURES)}"
        )
    else:
        await message.reply_text(
            f"╭━━━〔 👤 FREE PLAN 〕━━━╮\n"
            f"┃  🌌  {mention}\n"
            f"┃  ❌  Free Plan\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"<b>💠 Free Features:</b>\n{_feature_block(_FREE_FEATURES)}\n\n"
            f"<b>👑 Premium Upgrade:</b>\n{_feature_block(_PREMIUM_FEATURES, tick='⚡')}\n\n"
            "Contact the admin to upgrade. 📩"
        )


# ── /addpremium ───────────────────────────────────────────────────────────────

@Client.on_message(filters.command("addpremium") & filters.user(Config.OWNER_IDS))
async def cmd_add_premium(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply_text(
            "Usage: <code>/addpremium [user_id] [days]</code>\n"
            "Days = 0 → Lifetime"
        )
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    days = 30
    if len(parts) >= 3:
        try:
            days = max(0, int(parts[2]))
        except ValueError:
            return await message.reply_text("❌ Days must be a non-negative integer.")

    expiry = None if days == 0 else time.time() + days * 86400
    await db.grant_premium(target_id, expiry)

    exp_str = "Lifetime ∞" if not days else (
        f"{datetime.fromtimestamp(expiry, tz=timezone.utc).strftime('%d %b %Y')} ({days}d)"
    )

    try:
        await client.send_message(
            target_id,
            f"╭━━━〔 👑 PREMIUM ACTIVATED 〕━━━╮\n"
            f"┃  Plan     →  {'Lifetime ∞' if not days else f'{days} day(s)'}\n"
            f"┃  Expires  →  {exp_str}\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"{_feature_block(_PREMIUM_FEATURES)}"
        )
        dm = "✅ DM sent"
    except Exception:
        dm = "⚠️ Could not DM user"

    await message.reply_text(
        f"╭━━━〔 👑 PREMIUM GRANTED 〕━━━╮\n"
        f"┃  🆔  <code>{target_id}</code>\n"
        f"┃  📅  {exp_str}\n"
        f"┃  📨  {dm}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ── /removepremium ────────────────────────────────────────────────────────────

@Client.on_message(
    filters.command(["removepremium", "rempremium"]) & filters.user(Config.OWNER_IDS)
)
async def cmd_rem_premium(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply_text("Usage: /removepremium [user_id]")
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    await db.revoke_premium(target_id)
    try:
        await client.send_message(
            target_id,
            "╭━━━〔 👑 PREMIUM STATUS 〕━━━╮\n"
            "┃  ⚠️  Premium access revoked.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        dm = "✅ DM sent"
    except Exception:
        dm = "⚠️ Could not DM user"

    await message.reply_text(
        f"╭━━━〔 👑 PREMIUM REVOKED 〕━━━╮\n"
        f"┃  🆔  <code>{target_id}</code>\n"
        f"┃  📨  {dm}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ── /checkpremium ─────────────────────────────────────────────────────────────

@Client.on_message(filters.command("checkpremium") & filters.user(Config.OWNER_IDS))
async def cmd_check_premium(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply_text("Usage: /checkpremium [user_id]")
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID.")

    now     = time.time()
    is_prem = await db.is_premium(target_id, Config.OWNER_IDS)
    doc     = await db.get_user(target_id)
    expiry  = doc.get("premium_expiry")

    if target_id in Config.OWNER_IDS:
        return await message.reply_text(
            f"╭━━━〔 🛡 ADMIN 〕━━━╮\n"
            f"┃  🆔  <code>{target_id}</code>\n"
            f"┃  ⚡  Lifetime ∞ (Admin)\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if not is_prem:
        return await message.reply_text(
            f"╭━━━〔 👑 CHECK 〕━━━╮\n"
            f"┃  🆔  <code>{target_id}</code>\n"
            f"┃  ❌  Free Plan\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if expiry:
        dt        = datetime.fromtimestamp(expiry, tz=timezone.utc)
        days_left = max(0, int((expiry - now) / 86400))
        exp_line  = f"{dt.strftime('%d %b %Y')}  ({days_left}d left)"
    else:
        exp_line = "Lifetime ∞"

    await message.reply_text(
        f"╭━━━〔 👑 CHECK 〕━━━╮\n"
        f"┃  🆔  <code>{target_id}</code>\n"
        f"┃  ⭐  Active Premium\n"
        f"┃  📅  {exp_line}\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ── /premiumlist ──────────────────────────────────────────────────────────────

@Client.on_message(filters.command("premiumlist") & filters.user(Config.OWNER_IDS))
async def cmd_premium_list(client: Client, message: Message) -> None:
    db   = get_db()
    now  = time.time()

    cursor  = db.users.find({"premium": True})
    docs    = await cursor.to_list(length=200)

    lines   = ["╭━━━〔 👑 PREMIUM MEMBERS 〕━━━╮"]
    count   = lifetime = expired_count = 0

    for user in docs:
        uid    = user["_id"]
        expiry = user.get("premium_expiry")
        if expiry and expiry < now:
            await db.revoke_premium(uid)
            expired_count += 1
            continue
        if expiry:
            dt        = datetime.fromtimestamp(expiry, tz=timezone.utc)
            days_left = max(0, int((expiry - now) / 86400))
            lines.append(
                f"┃  👑  <code>{uid}</code>\n"
                f"┃      📅 {dt.strftime('%d %b %Y')} ({days_left}d)"
            )
        else:
            lifetime += 1
            lines.append(f"┃  ∞  <code>{uid}</code>  ·  Lifetime")
        count += 1

    if count == 0:
        lines.append("┃  ⚠️  No premium members yet.")

    lines.append("╰━━━━━━━━━━━━━━━━━━━━━━━━╯")
    lines.append(
        f"<blockquote>Active · {count}  ·  Lifetime · {lifetime}  ·  Auto-expired · {expired_count}</blockquote>"
    )
    await message.reply_text("\n".join(lines))
