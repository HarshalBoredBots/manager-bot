"""
manager/plugins/source_media_settings.py
══════════════════════════════════════════════════════════════════════════════
Per-user settings for rename extraction source and upload media type.

Commands
────────
  /setsource [filename | caption | both]
    Configure which text source is used when extracting episode/season/quality
    placeholders from the rename template.

  /setmedia [auto | document | video | audio]
    Configure how the worker uploads the finished file to Telegram.
    auto     → inferred from the incoming file type (default)
    document → always raw document (no streaming player)
    video    → send_video (streaming player)
    audio    → send_audio (music player)
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from helper.database import get_db

logger = logging.getLogger(__name__)

# ── Valid option sets ─────────────────────────────────────────────────────────

_VALID_SOURCE = ("filename", "caption", "both")
_VALID_MEDIA  = ("auto", "document", "video", "audio")


# ══════════════════════════════════════════════════════════════════════════════
# /setsource
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("setsource"))
async def cmd_setsource(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id

    parts = message.text.split(maxsplit=1)
    arg   = parts[1].strip().lower() if len(parts) > 1 else ""

    if not arg:
        current = await db.get_rename_source(user_id)
        return await message.reply_text(
            "╭━━━〔 🗂 RENAME SOURCE 〕━━━╮\n"
            f"┃  Current: <b>{current}</b>\n"
            "┃\n"
            "┃  <b>Usage:</b> /setsource [option]\n"
            "┃\n"
            "┃  <b>filename</b>  — use original file name\n"
            "┃  <b>caption</b>   — use message caption\n"
            "┃                  (fallback: filename)\n"
            "┃  <b>both</b>      — caption + filename combined\n"
            "┃                  (fallback: filename)\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if arg not in _VALID_SOURCE:
        return await message.reply_text(
            "╭━━━〔 ❌ INVALID OPTION 〕━━━╮\n"
            f"┃  <code>{arg}</code> is not a valid source.\n"
            "┃\n"
            "┃  Valid options:\n"
            "┃  <b>filename</b> · <b>caption</b> · <b>both</b>\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    await db.set_rename_source(user_id, arg)
    logger.info("[setsource] user=%s → %s", user_id, arg)

    await message.reply_text(
        "╭━━━〔 ✅ SOURCE UPDATED 〕━━━╮\n"
        f"┃  Rename source set to: <b>{arg}</b>\n"
        "┃\n"
        + (
            "┃  Template placeholders will be extracted\n"
            "┃  from the file's original name.\n"
            if arg == "filename" else
            "┃  Template placeholders will be extracted\n"
            "┃  from the message caption.\n"
            "┃  Falls back to filename if caption is empty.\n"
            if arg == "caption" else
            "┃  Template placeholders will be extracted\n"
            "┃  from caption + filename combined.\n"
            "┃  Falls back to filename if caption is empty.\n"
        ) +
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ══════════════════════════════════════════════════════════════════════════════
# /setmedia
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("setmedia"))
async def cmd_setmedia(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id

    parts = message.text.split(maxsplit=1)
    arg   = parts[1].strip().lower() if len(parts) > 1 else ""

    if not arg:
        raw     = await db.get_media_preference(user_id)
        current = raw if raw else "auto"
        return await message.reply_text(
            "╭━━━〔 🎞 MEDIA TYPE 〕━━━╮\n"
            f"┃  Current: <b>{current}</b>\n"
            "┃\n"
            "┃  <b>Usage:</b> /setmedia [option]\n"
            "┃\n"
            "┃  <b>auto</b>      — infer from file type (default)\n"
            "┃  <b>document</b>  — always raw document\n"
            "┃  <b>video</b>     — Telegram streaming player\n"
            "┃  <b>audio</b>     — Telegram music player\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    if arg not in _VALID_MEDIA:
        return await message.reply_text(
            "╭━━━〔 ❌ INVALID OPTION 〕━━━╮\n"
            f"┃  <code>{arg}</code> is not a valid media type.\n"
            "┃\n"
            "┃  Valid options:\n"
            "┃  <b>auto</b> · <b>document</b> · <b>video</b> · <b>audio</b>\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    # "auto" clears the preference (stored as None)
    db_value = None if arg == "auto" else arg
    await db.set_media_preference(user_id, db_value)
    logger.info("[setmedia] user=%s → %s (stored=%s)", user_id, arg, db_value)

    _desc = {
        "auto":     "Worker will infer the type from the original file.",
        "document": "Files will be uploaded as raw documents (no re-encode).",
        "video":    "Files will be uploaded via send_video (streaming player).",
        "audio":    "Files will be uploaded via send_audio (music player).",
    }
    await message.reply_text(
        "╭━━━〔 ✅ MEDIA TYPE UPDATED 〕━━━╮\n"
        f"┃  Upload type set to: <b>{arg}</b>\n"
        "┃\n"
        f"┃  {_desc[arg]}\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
