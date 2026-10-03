"""
manager/plugins/global_metadata.py
════════════════════════════════════════════════════════════════════════════
Owner-only commands for global metadata control.

Commands
────────
/gmeta              — show current global metadata status + fields
/gmeta on           — enable global metadata override
/gmeta off          — disable global metadata override
/gmeta set <field> <value>
                    — set a single global metadata field
                      e.g.  /gmeta set title My Show Title
/gmeta clear        — clear all global metadata field values
                      (does NOT disable — just empties the fields)

Fields supported (same as per-user metadata):
    title  artist  author  comment  audio  video  subtitle

When global metadata is ON:
    • ALL new jobs use the owner-configured fields instead of user fields.
    • User per-field settings are untouched — they return automatically
      when global metadata is turned OFF.
    • Jobs already queued have their metadata snapshot baked in at creation
      time (in batch.py) and are not retroactively affected.

When global metadata is OFF:
    • Each user's own metadata settings apply as usual.
    • No user data is modified.

Owner check: uses Config.OWNER_IDS — same as all other admin commands.
Storage: global_settings MongoDB collection — survives restarts.
Version: metadata_version is incremented on every set/clear so workers can
         detect mid-flight global metadata changes via the job document.
"""

from __future__ import annotations

import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from helper.database import get_db

logger = logging.getLogger(__name__)

# Valid field names (must match _META_DEFAULTS keys in database.py)
_VALID_FIELDS = {"title", "artist", "author", "comment", "audio", "video", "subtitle"}

_FIELD_LABELS = {
    "title":    "🏷️  Title",
    "artist":   "🎨  Artist",
    "author":   "✍️  Author",
    "comment":  "💬  Comment",
    "audio":    "🔊  Audio Track",
    "video":    "🎥  Video Track",
    "subtitle": "📝  Subtitle",
}


# ══════════════════════════════════════════════════════════════════════════════
# Panel builder
# ══════════════════════════════════════════════════════════════════════════════

async def _panel_text() -> str:
    db      = get_db()
    enabled = await db.get_global_metadata_enabled()
    fields  = await db.get_global_metadata_fields()
    version = await db.get_metadata_version()
    status  = "✅ ON — overrides all users" if enabled else "❌ OFF — users use own settings"

    lines = [
        "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮",
        f"┃  ⚡  Status   ·  {status}",
        f"┃  🔢  Version  ·  v{version}",
        "┣━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>💠 Global Fields:</b>",
    ]
    for key, label in _FIELD_LABELS.items():
        val = (fields.get(key) or "").strip()
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


# ══════════════════════════════════════════════════════════════════════════════
# /gmeta  command
# ══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("gmeta") & filters.user(Config.OWNER_IDS))
async def cmd_gmeta(client: Client, message: Message):
    """
    /gmeta              — status panel
    /gmeta on|off       — toggle
    /gmeta set f v      — set field f to value v
    /gmeta clear        — empty all fields
    """
    db    = get_db()
    parts = message.text.strip().split(maxsplit=3)
    sub   = parts[1].lower() if len(parts) > 1 else ""

    # ── /gmeta (no sub-command) → show panel ─────────────────────────────
    if not sub:
        return await message.reply_text(await _panel_text())

    # ── /gmeta on ─────────────────────────────────────────────────────────
    if sub == "on":
        await db.set_global_metadata_enabled(True)
        logger.info("[gmeta] Owner %s ENABLED global metadata", message.from_user.id)
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  ✅  Enabled — all new jobs\n"
            "┃      will use owner metadata.\n"
            "┃  💾  Persisted\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            + await _panel_text()
        )

    # ── /gmeta off ────────────────────────────────────────────────────────
    if sub == "off":
        await db.set_global_metadata_enabled(False)
        logger.info("[gmeta] Owner %s DISABLED global metadata", message.from_user.id)
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  ❌  Disabled — users return\n"
            "┃      to their own settings.\n"
            "┃  💾  Persisted\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    # ── /gmeta clear ──────────────────────────────────────────────────────
    if sub == "clear":
        await db.clear_global_metadata_fields()
        await db.increment_metadata_version()
        logger.info("[gmeta] Owner %s CLEARED global metadata fields", message.from_user.id)
        return await message.reply_text(
            "╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            "┃  🗑  All fields cleared.\n"
            "┃  ℹ️  Toggle state unchanged.\n"
            "┃  🔢  Version incremented.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    # ── /gmeta set <field> <value> ────────────────────────────────────────
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
        await db.set_global_metadata_field(field, value)
        await db.increment_metadata_version()
        logger.info("[gmeta] Owner %s SET %s = %r", message.from_user.id, field, value)
        label = _FIELD_LABELS.get(field, field)
        version = await db.get_metadata_version()
        return await message.reply_text(
            f"╭━━━〔 🌐 GLOBAL METADATA 〕━━━╮\n"
            f"┃  ✅  {label}\n"
            f"┃      → <code>{value}</code>\n"
            f"┃  💾  Persisted\n"
            f"┃  🔢  Version → v{version}\n"
            f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )

    # ── Unknown sub-command ────────────────────────────────────────────────
    await message.reply_text(
        "╭━━━〔 🌐 GLOBAL METADATA HELP 〕━━━╮\n"
        "┃  /gmeta          ·  show status\n"
        "┃  /gmeta on       ·  enable override\n"
        "┃  /gmeta off      ·  disable override\n"
        "┃  /gmeta set f v  ·  set field f to v\n"
        "┃  /gmeta clear    ·  empty all fields\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"<b>Fields:</b> <code>{', '.join(sorted(_VALID_FIELDS))}</code>"
    )
