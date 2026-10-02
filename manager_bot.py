"""
manager/manager_bot.py
══════════════════════════════════════════════════════════════════════════════
Manager Bot entry point — uses Bot(Client).run() pattern identical to the
original bot.py so Pyrogram plugin loading works correctly on Termux.

Key difference from the original:
  • String Session client is started inside Bot.start() for protocol I/O.
  • FairScheduler and ProtocolListener are wired up there too.
  • Bot().run() is called at module level — same as original.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import os
import sys

import pyrogram.utils
from aiohttp import web
from pyrogram import Client

from config import Config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
for _noisy in ("pyrogram", "aiohttp", "motor"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("manager")

# ── Pyrogram large-channel ID patch ──────────────────────────────────────────
pyrogram.utils.MIN_CHAT_ID    = -999_999_999_999
pyrogram.utils.MIN_CHANNEL_ID = -1_009_999_999_999


class Bot(Client):

    def __init__(self):
        super().__init__(
            name            = "manager_bot",
            api_id          = Config.API_ID,
            api_hash        = Config.API_HASH,
            bot_token       = Config.BOT_TOKEN,
            workers         = 4,
            plugins         = {"root": "plugins"},
            sleep_threshold = 30,
        )
        self.session_client = None
        self.db             = None
        self.scheduler      = None
        self.listener       = None   # FIX BUG 3: keep a reference so GC doesn't collect it
        self.uptime         = Config.BOT_UPTIME

    async def start(self):
        await super().start()
        me            = await self.get_me()
        self.mention  = me.mention
        self.username = me.username

        logger.info("[manager] Bot started: %s (@%s)", me.first_name, me.username)

        # ── 1. MongoDB ────────────────────────────────────────────────────────
        from helper.database import init_db
        self.db = init_db(Config.MONGO_URI, Config.DB_NAME)
        await self.db.ensure_indexes()

        from helper.staging import validate_output_channel
        if not await validate_output_channel(self, Config.WORKER_OUTPUT_CHANNEL_ID):
            logger.error(
                "[manager] WORKER_OUTPUT_CHANNEL_ID=%s not writable — "
                "check admin + Post Messages perm",
                Config.WORKER_OUTPUT_CHANNEL_ID,
            )
        logger.info("[manager] MongoDB ready — db=%s", Config.DB_NAME)

        # ── 2. String Session ─────────────────────────────────────────────────
        if not Config.STRING_SESSION:
            logger.error("[manager] STRING_SESSION not set — worker protocol disabled!")
        else:
            self.session_client = Client(
                name           = "manager_session",
                api_id         = Config.API_ID,
                api_hash       = Config.API_HASH,
                session_string = Config.STRING_SESSION,
                no_updates     = False,
                in_memory      = True,
            )
            await self.session_client.start()
            session_me = await self.session_client.get_me()
            logger.info(
                "[manager] String Session started: %s (id=%s)",
                session_me.first_name, session_me.id,
            )

            # ── 3. Scheduler ──────────────────────────────────────────────────
            from helper.scheduler import FairScheduler
            self.scheduler = FairScheduler(
                db               = self.db,
                control_group_id = Config.WORKER_CONTROL_GROUP_ID,
                session_client   = self.session_client,
            )
            self.scheduler.start()
            logger.info("[manager] Scheduler started")

            # ── 4. Protocol Listener ──────────────────────────────────────────
            # FIX BUG 3: Assign the ProtocolListener to self.listener so it is
            # kept alive for the Bot's lifetime.  Previously the return value
            # was discarded — Python would eventually garbage-collect the object
            # and with it the message handler registered in __init__, silently
            # stopping all incoming REGISTER/HEARTBEAT/RESULT/FAILED processing.
            from helper.listener import ProtocolListener
            self.listener = ProtocolListener(
                session_client    = self.session_client,
                bot_client        = self,
                db                = self.db,
                control_group_id  = Config.WORKER_CONTROL_GROUP_ID,
                output_channel_id = Config.WORKER_OUTPUT_CHANNEL_ID,
                dump_channel_id   = Config.CENTRAL_DUMP_CHANNEL_ID,
                admin_ids         = Config.OWNER_IDS,
            )
            logger.info("[manager] Protocol listener attached")

        # ── 5. Register bot commands ──────────────────────────────────────────
        try:
            from helper.bot_commands import update_bot_commands
            await update_bot_commands(self)
        except Exception as exc:
            logger.warning("[manager] Command registration failed (non-fatal): %s", exc)

        # ── 6. Health-check server ────────────────────────────────────────────
        async def _health(_):
            return web.Response(text="Manager OK")

        app = web.Application()
        app.router.add_get("/",       _health)
        app.router.add_get("/health", _health)
        runner = web.AppRunner(app)
        await runner.setup()
        port = Config.PORT
        for _ in range(5):
            try:
                await web.TCPSite(runner, "0.0.0.0", port).start()
                logger.info("[manager] Health server on port %d", port)
                break
            except OSError:
                logger.warning("[manager] Port %d busy — trying %d", port, port + 1)
                port += 1
        else:
            logger.warning("[manager] Health server could not bind — continuing")

        # ── 7. Notify admins ──────────────────────────────────────────────────
        for owner_id in Config.OWNER_IDS:
            try:
                await self.send_message(
                    owner_id,
                    f"╭━━━〔 ✅ MANAGER ONLINE 〕━━━╮\n"
                    f"┃  🤖  {me.mention}\n"
                    f"┃  📡  String Session ready\n"
                    f"┃  🔧  Scheduler running\n"
                    f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
                )
            except Exception as exc:
                logger.debug("[manager] Could not notify owner %s: %s", owner_id, exc)

        if Config.LOG_CHANNEL:
            try:
                from datetime import datetime, timezone as _tz, timedelta
                try:
                    from pytz import timezone
                    ist = datetime.now(timezone("Asia/Kolkata"))
                except ImportError:
                    ist = datetime.now(_tz(timedelta(hours=5, minutes=30)))
                await self.send_message(
                    Config.LOG_CHANNEL,
                    f"╭━━━〔 🌌 MANAGER BOOT 〕━━━╮\n"
                    f"┃  🤖  {me.mention}\n"
                    f"┃  📅  {ist.strftime('%d %B %Y')}\n"
                    f"┃  ⏰  {ist.strftime('%I:%M:%S %p')} IST\n"
                    f"╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
                )
            except Exception as exc:
                logger.debug("[manager] Log channel notify failed: %s", exc)

        logger.info("[manager] Startup complete — ready")

    async def stop(self):
        if self.scheduler:
            self.scheduler.stop()
        if self.session_client:
            try:
                await self.session_client.stop()
            except Exception:
                pass
        await super().stop()
        logger.info("[manager] Stopped")


Bot().run()
