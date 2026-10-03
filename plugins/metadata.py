"""
manager/plugins/metadata.py
════════════════════════════════════════════════════════════════════════════
Per-user metadata configuration panel.

Command: /metadata
    Opens an interactive panel where each user can configure the metadata
    fields that will be injected into their renamed files.  Mirrors the
    full metadata panel from RenameNaruto.

Storage: user document in MongoDB (metadata bool + metadata_fields dict).
DB instance: manager-bot's get_db() singleton (ManagerDB).
"""

import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from helper.database import get_db

_state:  dict[int, str | None] = {}
_panels: dict[int, Message]    = {}

FIELDS = {
    "mt_title":    ("🏷️ Title",      "title",    "Title",       "-metadata title="),
    "mt_artist":   ("🎨 Artist",      "artist",   "Artist",      "-metadata artist="),
    "mt_author":   ("✍️ Author",       "author",   "Author",      "-metadata author="),
    "mt_comment":  ("💬 Comment",     "comment",  "Comment",     "-metadata comment="),
    "mt_audio":    ("🔊 Audio Track", "audio",    "Audio Track", "-metadata:s:a title="),
    "mt_video":    ("🎥 Video Track", "video",    "Video Track", "-metadata:s:v title="),
    "mt_subtitle": ("📝 Subtitle",    "subtitle", "Subtitle",    "-metadata:s:s title="),
}


def _keyboard() -> InlineKeyboardMarkup:
    keys = list(FIELDS.items())
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(keys[i][1][0], callback_data=keys[i][0])]
        if i + 1 < len(keys):
            row.append(InlineKeyboardButton(keys[i + 1][1][0], callback_data=keys[i + 1][0]))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🔴 Disable", callback_data="mt_disable"),
        InlineKeyboardButton("🟢 Enable",  callback_data="mt_enable"),
        InlineKeyboardButton("✖️ Close",   callback_data="mt_close"),
    ])
    return InlineKeyboardMarkup(rows)


async def _panel_text(user_id: int) -> str:
    db      = get_db()
    enabled = await db.get_metadata(user_id)
    fields  = await db.get_metadata_fields(user_id)
    status  = "✅ Enabled" if enabled else "❌ Disabled"
    lines   = [
        f"╭━━━〔 🧬 METADATA ENGINE 〕━━━╮",
        f"┃  ⚡  Status  ·  {status}",
        f"┣━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>💠 Injected Fields:</b>",
    ]
    for _, (label, key, display, _flag) in FIELDS.items():
        val = (fields.get(key) or "").strip()
        lines.append(f"┃  {label}  ·  {'<code>' + val + '</code>' if val else '<i>—</i>'}")
    lines.append("╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n<i>⚡ Tap a field to configure.\nEnable to inject on rename.</i>")
    return "\n".join(lines)


@Client.on_message(filters.private & filters.command("metadata"))
async def cmd_metadata(client: Client, message: Message):
    user_id = message.from_user.id
    _state.pop(user_id, None)
    text  = await _panel_text(user_id)
    kb    = _keyboard()
    panel = await message.reply_text(text, reply_markup=kb)
    _panels[user_id] = panel


@Client.on_callback_query(filters.regex(r"^mt_"))
async def cb_metadata(client: Client, query: CallbackQuery):
    db      = get_db()
    user_id = query.from_user.id
    data    = query.data

    async def _refresh():
        text = await _panel_text(user_id)
        try:
            await query.message.edit_text(text, reply_markup=_keyboard())
        except Exception:
            pass

    if data in FIELDS:
        label, key, display, fflag = FIELDS[data]
        _state[user_id]  = f"waiting_{key}"
        _panels[user_id] = query.message
        await query.answer(f"Send your {display} value")
        try:
            await query.message.edit_text(
                f"◈ <b>Set {display}</b>\n\n"
                f"Type the value and send it.\n"
                f"Send /cancel to go back.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚫 Cancel", callback_data="mt_cancel")
                ]]),
            )
        except Exception:
            pass
        return

    if data == "mt_cancel":
        _state.pop(user_id, None)
        await query.answer("Cancelled.")
        await _refresh()
        return

    if data == "mt_enable":
        fields = await db.get_metadata_fields(user_id)
        if not any((v or "").strip() for v in fields.values()):
            await query.answer("⚠️ Set at least one field first.", show_alert=True)
            return
        await db.set_metadata(user_id, value=True)
        await query.answer("✅ Metadata enabled.")
        await _refresh()
        return

    if data == "mt_disable":
        _state.pop(user_id, None)
        await db.set_metadata(user_id, value=False)
        await query.answer("Metadata disabled.")
        await _refresh()
        return

    if data == "mt_close":
        _state.pop(user_id, None)
        _panels.pop(user_id, None)
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    await query.answer()


@Client.on_message(
    filters.private & filters.text & ~filters.command([
        "start", "metadata", "cancel", "autorename", "done", "cancelall",
        "queuestatus", "clearqueue", "gmeta",
        "set_caption", "del_caption",
        "set_prefix", "del_prefix",
        "set_suffix", "del_suffix",
        "view_thumb", "del_thumb",
        "premium", "leaderboard", "history", "status", "ping", "help",
    ]),
    group=1,
)
async def capture_metadata_input(client: Client, message: Message):
    db      = get_db()
    user_id = message.from_user.id
    state   = _state.get(user_id)
    if not state or not state.startswith("waiting_"):
        return
    if message.text.strip().lower() in ("/cancel", "cancel"):
        _state.pop(user_id, None)
        ack = await message.reply_text("◈ <b>Metadata</b>\n\n<blockquote>Cancelled.</blockquote>")
        await asyncio.sleep(3)
        try:
            await ack.delete()
            await message.delete()
        except Exception:
            pass
        return
    field_key = state[len("waiting_"):]
    await db.set_metadata_field(user_id, field_key, message.text.strip())
    _state.pop(user_id, None)
    try:
        await message.delete()
    except Exception:
        pass
    text      = await _panel_text(user_id)
    panel_msg = _panels.get(user_id)
    if panel_msg:
        try:
            await panel_msg.edit_text(text, reply_markup=_keyboard())
            return
        except Exception:
            pass
    panel = await client.send_message(user_id, text, reply_markup=_keyboard())
    _panels[user_id] = panel


@Client.on_message(filters.private & filters.command("cancel"))
async def cmd_cancel(client: Client, message: Message):
    if _state.pop(message.from_user.id, None):
        await message.reply_text("◈ <b>Metadata</b>\n\n<blockquote>Cancelled  ·  use /metadata to reopen.</blockquote>")
    else:
        await message.reply_text("Nothing active to cancel.")
