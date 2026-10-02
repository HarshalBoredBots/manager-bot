"""
manager/plugins/metadata.py
══════════════════════════════════════════════════════════════════════════════
Per-user metadata injection panel.

Adapted from original plugins/metadata.py — all jishubotz calls replaced
with get_db(), no helper.ui dependency.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from helper.database import get_db

# ── Per-user FSM state (in-memory, resets on restart — acceptable) ─────────────
_state:  dict[int, str]     = {}   # user_id → "waiting_<field_key>"
_panels: dict[int, Message] = {}   # user_id → panel Message


FIELDS = {
    "mt_title":    ("🏷️ Title",       "title",    "Title"),
    "mt_artist":   ("🎨 Artist",       "artist",   "Artist"),
    "mt_author":   ("✍️ Author",        "author",   "Author"),
    "mt_comment":  ("💬 Comment",      "comment",  "Comment"),
    "mt_audio":    ("🔊 Audio Track",  "audio",    "Audio Track"),
    "mt_video":    ("🎥 Video Track",  "video",    "Video Track"),
    "mt_subtitle": ("📝 Subtitle",     "subtitle", "Subtitle"),
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
    db     = get_db()
    doc    = await db.get_user(user_id)
    enabled = doc.get("metadata", False)
    fields  = doc.get("metadata_fields") or {}
    status  = "✅ Enabled" if enabled else "❌ Disabled"

    lines = [
        "╭━━━〔 🧬 METADATA ENGINE 〕━━━╮",
        f"┃  ⚡  Status  ·  {status}",
        "┣━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>💠 Injected Fields:</b>",
    ]
    for _, (label, key, _display) in FIELDS.items():
        val = (fields.get(key) or "").strip()
        lines.append(
            f"┃  {label}  ·  {'<code>' + val + '</code>' if val else '<i>—</i>'}"
        )
    lines.append(
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "<i>⚡ Tap a field to configure.\nEnable to inject on rename.</i>"
    )
    return "\n".join(lines)


@Client.on_message(filters.private & filters.command("metadata"))
async def cmd_metadata(client: Client, message: Message) -> None:
    user_id = message.from_user.id
    _state.pop(user_id, None)
    text  = await _panel_text(user_id)
    panel = await message.reply_text(text, reply_markup=_keyboard())
    _panels[user_id] = panel


@Client.on_callback_query(filters.regex(r"^mt_"))
async def cb_metadata(client: Client, query: CallbackQuery) -> None:
    db      = get_db()
    user_id = query.from_user.id
    data    = query.data

    async def _refresh():
        text = await _panel_text(user_id)
        try:
            await query.message.edit_text(text, reply_markup=_keyboard())
        except Exception:
            pass

    # Field edit request
    if data in FIELDS:
        label, key, display = FIELDS[data]
        _state[user_id]  = f"waiting_{key}"
        _panels[user_id] = query.message
        await query.answer(f"Send your {display} value")
        try:
            await query.message.edit_text(
                f"◈ <b>Set {display}</b>\n\n"
                "Type the value and send it.\n"
                "Send /cancel to go back.",
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
        doc    = await db.get_user(user_id)
        fields = doc.get("metadata_fields") or {}
        if not any((v or "").strip() for v in fields.values()):
            return await query.answer("⚠️ Set at least one field first.", show_alert=True)
        await db.users.update_one(
            {"_id": int(user_id)},
            {"$set": {"metadata": True}},
            upsert=True,
        )
        await query.answer("✅ Metadata enabled.")
        await _refresh()
        return

    if data == "mt_disable":
        _state.pop(user_id, None)
        await db.users.update_one(
            {"_id": int(user_id)},
            {"$set": {"metadata": False}},
            upsert=True,
        )
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


# Text capture for metadata field input
@Client.on_message(
    filters.private & filters.text & ~filters.command([
        "start", "metadata", "cancel", "autorename", "done", "status",
        "set_prefix", "del_prefix", "set_suffix", "del_suffix",
        "set_caption", "del_caption", "view_thumb", "del_thumb",
        "ban", "unban", "broadcast", "gmeta", "addpremium",
        "removepremium", "checkpremium", "premiumlist", "leaderboard",
    ]),
    group=1,
)
async def capture_metadata_input(client: Client, message: Message) -> None:
    db      = get_db()
    user_id = message.from_user.id
    state   = _state.get(user_id)
    if not state or not state.startswith("waiting_"):
        return

    if message.text.strip().lower() in ("/cancel", "cancel"):
        _state.pop(user_id, None)
        ack = await message.reply_text("Cancelled.")
        await asyncio.sleep(3)
        try:
            await ack.delete()
            await message.delete()
        except Exception:
            pass
        return

    field_key = state[len("waiting_"):]
    await db.users.update_one(
        {"_id": int(user_id)},
        {"$set": {f"metadata_fields.{field_key}": message.text.strip()}},
        upsert=True,
    )
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
async def cmd_cancel(client: Client, message: Message) -> None:
    if _state.pop(message.from_user.id, None):
        await message.reply_text("Cancelled — use /metadata to reopen.")
    else:
        await message.reply_text("Nothing active to cancel.")
