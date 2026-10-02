"""
manager/plugins/global_metadata.py
══════════════════════════════════════════════════════════════════════════════
Owner-only global metadata override.
Adapted from original — jishubotz → get_db(), Config.ADMIN → Config.OWNER_IDS.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from helper.database import get_db

logger = logging.getLogger(__name__)

_VALID_FIELDS = {"title", "artist", "author", "comment", "audio", "video", "subtitle"}

_FIELD_LABELS = {
    "title":    "🏷️  Title",
    "artist":   "🎨  Artist",
    "author":   "✍️  Author",
    "comment":  "💬  Comment",
    "audio":    "🔊  Audio Track Title",
    "video":    "🎥  Video Track Title",
    "subtitle": "📝  Subtitle Track Title",
}


async def _panel_text() -> str:
    db      = get_db()
    doc     = await db.settings.find_one({"_id": "global_metadata"}) or {}
    enabled = bool(doc.get("enabled", False))
    status  = "✅ ON — overrides all users" if enabled else "❌ OFF — users use own settings"

    lines = [
        "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮",
        f"┃  ⚡  Status  ·  {status}",
        "┣━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>💠 Global Fields:</b>",
    ]
    for key, label in _FIELD_LABELS.items():
        val     = (doc.get(key) or "").strip()
        display = f"<code>{val}</code>" if val else "<i>—</i>"
        lines.append(f"┃  {label}  ·  {display}")
    lines += [
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        "<b>Usage:</b>",
        "  <code>/gmeta on</code>  · <code>/gmeta off</code>",
        "  <code>/gmeta set title My Show Title</code>",
        "  <code>/gmeta clear</code>  (empty fields, keep state)",
    ]
    return "\n".join(lines)


@Client.on_message(filters.command("gmeta") & filters.user(Config.OWNER_IDS))
async def cmd_gmeta(client: Client, message: Message) -> None:
    db    = get_db()
    parts = message.text.strip().split(maxsplit=3)
    sub   = parts[1].lower() if len(parts) > 1 else ""

    if not sub:
        return await message.reply_text(await _panel_text())

    if sub == "on":
        await db.settings.update_one(
            {"_id": "global_metadata"},
            {"$set": {"enabled": True}},
            upsert=True,
        )
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  ✅  Enabled — all new jobs\n"
            "┃      will use owner metadata.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            + await _panel_text()
        )

    if sub == "off":
        await db.settings.update_one(
            {"_id": "global_metadata"},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  ❌  Disabled — users return\n"
            "┃      to their own settings.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if sub == "clear":
        await db.settings.update_one(
            {"_id": "global_metadata"},
            {"$set": {k: "" for k in _VALID_FIELDS}},
            upsert=True,
        )
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  🗑  All fields cleared.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if sub == "set":
        if len(parts) < 4:
            return await message.reply_text(
                "❌ Usage: <code>/gmeta set &lt;field&gt; &lt;value&gt;</code>\n\n"
                f"Valid fields: <code>{', '.join(sorted(_VALID_FIELDS))}</code>"
            )
        field = parts[2].lower()
        value = parts[3].strip()
        if field not in _VALID_FIELDS:
            return await message.reply_text(
                f"❌ Unknown field <code>{field}</code>.\n"
                f"Valid: <code>{', '.join(sorted(_VALID_FIELDS))}</code>"
            )
        await db.settings.update_one(
            {"_id": "global_metadata"},
            {"$set": {field: value}, "$inc": {"version": 1}},
            upsert=True,
        )
        label = _FIELD_LABELS.get(field, field)
        return await message.reply_text(
            f"╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            f"┃  ✅  {label}\n"
            f"┃      → <code>{value}</code>\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    await message.reply_text(
        "╭━━━〔 🌐 GLOBAL METADATA HELP 〕━━━╮\n"
        "┃  /gmeta          ·  show status\n"
        "┃  /gmeta on       ·  enable override\n"
        "┃  /gmeta off      ·  disable override\n"
        "┃  /gmeta set f v  ·  set field f to v\n"
        "┃  /gmeta clear    ·  empty all fields\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"<b>Fields:</b> <code>{', '.join(sorted(_VALID_FIELDS))}</code>"
    )
