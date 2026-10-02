"""
manager/plugins/thumbnail.py
"""
from __future__ import annotations
import logging, os, tempfile
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from config import Config
from helper.database import get_db
from helper.imgbb import upload_to_imgbb

logger = logging.getLogger(__name__)

# In-memory: user_id → file_id of the photo awaiting confirmation
_pending: dict[int, str] = {}


@Client.on_message(filters.private & filters.photo)
async def handle_photo(client: Client, message: Message) -> None:
    db = get_db()
    if await db.is_banned(message.from_user.id):
        return
    _pending[message.from_user.id] = message.photo.file_id
    await message.reply_text(
        "╭━━━〔 🖼️ THUMBNAIL 〕━━━╮\n"
        "┃  Save this as your thumbnail?\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Save Thumbnail", callback_data="save_thumb"),
            InlineKeyboardButton("❌ Ignore",          callback_data="ignore_thumb"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^save_thumb$"))
async def cb_save_thumb(client: Client, update: CallbackQuery) -> None:
    db      = get_db()
    user_id = update.from_user.id
    file_id = _pending.get(user_id)
    if not file_id:
        await update.answer("⚠️ Photo expired — please send it again.", show_alert=True)
        return
    await update.answer("Uploading…")
    progress_msg = await update.message.edit_text("⏳ Downloading photo…")
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = await client.download_media(
            file_id, file_name=os.path.join(tmpdir, "thumb.jpg")
        )
        await progress_msg.edit_text("⏳ Uploading to ImgBB…")
        url = await upload_to_imgbb(local_path)
    _pending.pop(user_id, None)
    if url:
        await db.set_thumbnail_url(user_id, url)
        await progress_msg.edit_text(
            "╭━━━〔 ✅ THUMBNAIL SAVED 〕━━━╮\n"
            "┃  🖼️  Uploaded to ImgBB\n"
            "┃  Applied to all renamed files.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "Use /del_thumb to remove it."
        )
    else:
        await db.set_thumbnail_url(user_id, None)
        await progress_msg.edit_text(
            "╭━━━〔 ⚠️ IMGBB UNAVAILABLE 〕━━━╮\n"
            "┃  Thumbnail could not be uploaded.\n"
            "┃  Ask the admin to set IMGBB_API_KEY.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )


@Client.on_callback_query(filters.regex(r"^ignore_thumb$"))
async def cb_ignore_thumb(client: Client, update: CallbackQuery) -> None:
    _pending.pop(update.from_user.id, None)
    await update.answer("Photo ignored.")
    try:
        await update.message.delete()
    except Exception:
        pass


@Client.on_message(filters.private & filters.command(["view_thumb", "viewthumb"]))
async def cmd_view_thumb(client: Client, message: Message) -> None:
    db  = get_db()
    url = await db.get_thumbnail_url(message.from_user.id)
    if url:
        try:
            await client.send_photo(
                message.chat.id, photo=url,
                caption=(
                    "╭━━━〔 🖼️ THUMBNAIL 〕━━━╮\n"
                    "┃  ✅  Active — applied to all jobs\n"
                    "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                    "➤  /del_thumb  to remove"
                ),
            )
            return
        except Exception:
            await db.set_thumbnail_url(message.from_user.id, None)
    await message.reply_text(
        "╭━━━〔 🖼️ THUMBNAIL 〕━━━╮\n"
        "┃  ⚠️  Not set\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "Send any photo and tap <b>Save Thumbnail</b>."
    )


@Client.on_message(filters.private & filters.command(["del_thumb", "delthumb"]))
async def cmd_del_thumb(client: Client, message: Message) -> None:
    db = get_db()
    await db.set_thumbnail_url(message.from_user.id, None)
    await message.reply_text(
        "╭━━━〔 🖼️ THUMBNAIL 〕━━━╮\n"
        "┃  ✅  Cleared\n"
        "┃  Files will use original cover art.\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
