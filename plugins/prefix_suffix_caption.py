"""
manager/plugins/prefix_suffix_caption.py
══════════════════════════════════════════════════════════════════════════════
Prefix, suffix, and caption commands.
Adapted from original — jishubotz → get_db(), no helper.ui dependency.
══════════════════════════════════════════════════════════════════════════════
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from helper.database import get_db


# ══════════════════════════════════════════════════════════════════════════════
# PREFIX
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("set_prefix"))
async def set_prefix(client: Client, message: Message) -> None:
    if len(message.command) < 2:
        return await message.reply_text(
            "╭━━━〔 🏷️ PREFIX 〕━━━╮\n"
            "┃  Prepended to every filename stem.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  Usage: <code>/set_prefix @Channel</code>"
        )
    prefix = message.text.split(None, 1)[1].strip()
    await get_db().set_prefix(message.from_user.id, prefix)
    await message.reply_text(
        "╭━━━〔 🏷️ PREFIX SET 〕━━━╮\n"
        f"┃  <code>{prefix}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


@Client.on_message(filters.private & filters.command("see_prefix"))
async def see_prefix(client: Client, message: Message) -> None:
    v = await get_db().get_prefix(message.from_user.id)
    if v:
        await message.reply_text(
            f"╭━━━〔 🏷️ PREFIX 〕━━━╮\n┃  <code>{v}</code>\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  /del_prefix  to remove"
        )
    else:
        await message.reply_text(
            "╭━━━〔 🏷️ PREFIX 〕━━━╮\n┃  ⚠️  Not set\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  /set_prefix  to configure"
        )


@Client.on_message(filters.private & filters.command("del_prefix"))
async def del_prefix(client: Client, message: Message) -> None:
    if not await get_db().get_prefix(message.from_user.id):
        return await message.reply_text("⚠️ No prefix is set.")
    await get_db().set_prefix(message.from_user.id, None)
    await message.reply_text(
        "╭━━━〔 🏷️ PREFIX 〕━━━╮\n┃  ✅  Cleared.\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ══════════════════════════════════════════════════════════════════════════════
# SUFFIX
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("set_suffix"))
async def set_suffix(client: Client, message: Message) -> None:
    if len(message.command) < 2:
        return await message.reply_text(
            "╭━━━〔 🏷️ SUFFIX 〕━━━╮\n"
            "┃  Appended before file extension.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  Usage: <code>/set_suffix [720p]</code>"
        )
    suffix = message.text.split(None, 1)[1].strip()
    await get_db().set_suffix(message.from_user.id, suffix)
    await message.reply_text(
        "╭━━━〔 🏷️ SUFFIX SET 〕━━━╮\n"
        f"┃  <code>{suffix}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


@Client.on_message(filters.private & filters.command("see_suffix"))
async def see_suffix(client: Client, message: Message) -> None:
    v = await get_db().get_suffix(message.from_user.id)
    if v:
        await message.reply_text(
            f"╭━━━〔 🏷️ SUFFIX 〕━━━╮\n┃  <code>{v}</code>\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  /del_suffix  to remove"
        )
    else:
        await message.reply_text(
            "╭━━━〔 🏷️ SUFFIX 〕━━━╮\n┃  ⚠️  Not set\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  /set_suffix  to configure"
        )


@Client.on_message(filters.private & filters.command("del_suffix"))
async def del_suffix(client: Client, message: Message) -> None:
    if not await get_db().get_suffix(message.from_user.id):
        return await message.reply_text("⚠️ No suffix is set.")
    await get_db().set_suffix(message.from_user.id, None)
    await message.reply_text(
        "╭━━━〔 🏷️ SUFFIX 〕━━━╮\n┃  ✅  Cleared.\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CAPTION
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.private & filters.command("set_caption"))
async def set_caption(client: Client, message: Message) -> None:
    if len(message.command) == 1:
        return await message.reply_text(
            "╭━━━〔 📝 CAPTION ENGINE 〕━━━╮\n"
            "┃  Attach custom text to every file.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "<b>⚡ Variables</b>\n"
            "┃  <code>{filename}</code>  ·  renamed name\n"
            "┃  <code>{filesize}</code>  ·  file size\n\n"
            "➤  Usage: <code>/set_caption {filename}\n📦 {filesize}</code>"
        )
    caption = message.text.split(" ", 1)[1]
    await get_db().set_caption(message.from_user.id, caption)
    await message.reply_text(
        "╭━━━〔 📝 CAPTION SAVED 〕━━━╮\n"
        f"┃  <code>{caption}</code>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


@Client.on_message(filters.private & filters.command("del_caption"))
async def del_caption(client: Client, message: Message) -> None:
    if not await get_db().get_caption(message.from_user.id):
        return await message.reply_text("⚠️ No caption template is set.")
    await get_db().set_caption(message.from_user.id, None)
    await message.reply_text(
        "╭━━━〔 📝 CAPTION ENGINE 〕━━━╮\n┃  ✅  Template removed.\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )


@Client.on_message(filters.private & filters.command("see_caption"))
async def see_caption(client: Client, message: Message) -> None:
    caption = await get_db().get_caption(message.from_user.id)
    if caption:
        await message.reply_text(
            "╭━━━〔 📝 CAPTION ENGINE 〕━━━╮\n"
            f"┃  <code>{caption}</code>\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    else:
        await message.reply_text(
            "╭━━━〔 📝 CAPTION ENGINE 〕━━━╮\n┃  ⚠️  No template set.\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "➤  /set_caption  to configure one"
        )
