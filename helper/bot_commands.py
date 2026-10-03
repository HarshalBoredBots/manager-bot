"""
manager/helper/bot_commands.py
═══════════════════════════════════════════════════════════════════════════════
Registers bot commands with Telegram via set_bot_commands.
Skipped if commands haven't changed since last boot (saves an API call).
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from pyrogram import Client
from pyrogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

logger = logging.getLogger(__name__)

# ── User-visible commands ─────────────────────────────────────────────────────
_USER_COMMANDS = [
    BotCommand("start",          "Start the bot"),
    BotCommand("autorename",     "Open a rename batch with a template"),
    BotCommand("done",           "Close the current batch"),
    BotCommand("cancelall",      "Cancel all queued jobs in the current batch"),
    BotCommand("queuestatus",    "Live queue and worker status"),
    BotCommand("metadata",       "Configure your metadata injection fields"),
    BotCommand("set_prefix",     "Set filename prefix"),
    BotCommand("del_prefix",     "Remove filename prefix"),
    BotCommand("set_suffix",     "Set filename suffix"),
    BotCommand("del_suffix",     "Remove filename suffix"),
    BotCommand("set_caption",    "Set custom file caption"),
    BotCommand("del_caption",    "Remove custom caption"),
    BotCommand("view_thumb",     "View your saved thumbnail"),
    BotCommand("del_thumb",      "Delete your saved thumbnail"),
    BotCommand("premium",        "Check premium status"),
    BotCommand("leaderboard",    "Top renamers leaderboard"),
    BotCommand("history",        "Your last 20 renamed files"),
    BotCommand("status",         "Worker pool status"),
    BotCommand("ping",           "Check bot latency"),
    BotCommand("help",           "Help and command list"),
]

# ── Admin-only commands (registered per-chat for owner IDs) ───────────────────
_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand("gmeta",          "Manage global metadata override (admin)"),
    BotCommand("clearqueue",     "Wipe all queued jobs (admin)"),
]

_last_hash: int | None = None


async def update_bot_commands(client: Client, owner_ids: list[int] | None = None) -> None:
    global _last_hash
    # Only bot accounts can set commands — never call on a user/string session
    me = await client.get_me()
    if not me.is_bot:
        logger.debug("[commands] Skipping set_bot_commands — client is a user account")
        return

    current_hash = hash(tuple((c.command, c.description) for c in _USER_COMMANDS))
    if current_hash == _last_hash:
        return

    try:
        # Set default (user-visible) commands for everyone
        await client.set_bot_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())

        # Set extended admin commands for each owner chat
        if owner_ids:
            for uid in owner_ids:
                try:
                    await client.set_bot_commands(
                        _ADMIN_COMMANDS,
                        scope=BotCommandScopeChat(chat_id=uid),
                    )
                except Exception as exc:
                    logger.debug("[commands] Could not set admin scope for %s: %s", uid, exc)

        _last_hash = current_hash
        logger.info(
            "[commands] Bot commands registered (%d user + %d admin commands)",
            len(_USER_COMMANDS), len(_ADMIN_COMMANDS),
        )
    except Exception as exc:
        logger.warning("[commands] set_bot_commands failed (non-fatal): %s", exc)
