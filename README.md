# Manager Rename Bot

The central coordinator in a distributed Telegram file-rename system. It accepts rename jobs from users, schedules them fairly across multiple Worker bots, tracks the full job lifecycle in MongoDB, and delivers completed files back to users.

> **Authors:** [@Lance_Arthur](https://t.me/Lance_Arthur) · [@naruto0927](https://t.me/naruto0927)
>
> Works in tandem with **Worker Rename Bot** — the Manager dispatches jobs; Workers execute them.

---

## Features

- **Fair-queue scheduler** — round-robin dispatch across users so no one's 100-file queue starves another user's 5 files; worker selection weighted by active job count
- **Full job lifecycle tracking** — every file moves through states: `QUEUED → ASSIGNED → ACKED → DOWNLOADING → PROCESSING → UPLOADING → UPLOADED → DELIVERING → DONE` (or `FAILED`)
- **Lease mechanism** — expired/stuck jobs are automatically re-queued so a crashed worker never permanently loses a file
- **Worker registry** — tracks all connected workers with heartbeat monitoring; marks workers `OFFLINE` when heartbeats stop
- **Per-user settings** — thumbnail, caption, prefix/suffix, metadata fields, rename mode, format template, output format, dump channel
- **Batch rename** — `/autorename` … `/done` cycle for bulk renaming
- **Premium system** — premium user management with owner controls
- **Global metadata** — owner-set metadata applied to all jobs
- **Template engine** — configurable rename templates
- **MediaInfo** — rich file info display
- **Leaderboard** — tracks rename stats across users/workers
- **ImgBB thumbnails** — cross-worker thumbnail sharing via ImgBB API
- **Health-check endpoint** — `/health` HTTP route for Render/Koyeb keep-alive
- **String Session** — used exclusively for reading/writing protocol messages in the Worker Control Group; never for file transfers

---

## Architecture

```
User
 │  sends file + rename request
 ▼
Manager Bot  ──── MongoDB ────────────────────────────────┐
 │  (schedules job, writes TASK to Control Group)         │
 ▼                                                        │
Worker Control Group (Telegram)                           │
 │  Workers poll via their own protocol handler           │
 ▼                                                        │
Worker Bot(s)  ── download → FFmpeg → upload ─────────────┤
 │  posts MSG_RESULT to Control Group                     │
 ▼                                                        │
Worker Output Channel                                     │
 │  Manager reads file, copies to user                   ◄┘
 ▼
User receives renamed file
```

The Manager uses a **Pyrogram Bot Client** for user interaction and a **String Session** client for sending/reading protocol JSON messages in the Worker Control Group.

---

## Project Structure

```
manager-bot-main/
├── manager_bot.py              # Entry point — startup, signal handling
├── config.py                   # All config loaded from environment variables
├── route.py                    # aiohttp health-check route (/health)
├── requirements.txt
├── runtime.txt                 # Python 3.10.15
├── Procfile                    # Heroku process definition
├── helper/
│   ├── bot_commands.py         # /start, /help command setup
│   ├── database.py             # MongoDB layer (users, jobs, workers, batches)
│   ├── imgbb.py                # ImgBB thumbnail upload helper
│   ├── listener.py             # Protocol listener — reads Worker Control Group
│   ├── scheduler.py            # Fair-queue scheduler & lease recovery
│   ├── staging.py              # Staging area: copy file from output channel to user
│   └── utils.py                # Shared utilities
├── plugins/
│   ├── admin.py                # Owner-only admin commands
│   ├── batch.py                # /autorename … /done batch flow
│   ├── global_metadata.py      # Owner-set global metadata
│   ├── leaderboard.py          # Rename stats leaderboard
│   ├── metadata.py             # Per-user metadata settings
│   ├── mi.py                   # MediaInfo display
│   ├── prefix_suffix_caption.py# Prefix, suffix, caption settings
│   ├── premium.py              # Premium user management
│   ├── source_media_settings.py# Input media type settings
│   ├── start.py                # /start handler
│   ├── template_engine.py      # Rename template parser/renderer
│   └── thumbnail.py            # Per-user thumbnail management
└── shared/
    └── protocol.py             # Wire protocol (JSON messages, job states)
```

---

## Wire Protocol

All messages in the Worker Control Group are plain-text JSON with a `type` field.

| Direction | Message Type | Description |
|---|---|---|
| Manager → Workers | `TASK` | Assign a job to a specific worker |
| Worker → Manager | `REGISTER` | Worker announces itself on startup |
| Worker → Manager | `HEARTBEAT` | Periodic liveness ping |
| Worker → Manager | `ACK` | Worker accepted the task |
| Worker → Manager | `STATE` | Job state transition update |
| Worker → Manager | `RESULT` | Worker finished and uploaded the file |
| Worker → Manager | `FAILED` | Worker could not complete the job |

---

## Requirements

- Python 3.10.15
- MongoDB (Atlas or any URI)
- Telegram API credentials + a String Session

### Python Dependencies

```
pyrogram==2.0.106
TgCrypto
motor
dnspython
aiohttp
pytz
Pillow
psutil
```

---

## Environment Variables

Copy `.env.example` to `.env` for local development. **Never commit `.env`.**

### Required

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID (from my.telegram.org) |
| `API_HASH` | Telegram API hash |
| `BOT_TOKEN` | Manager bot token (from @BotFather) |
| `STRING_SESSION` | Pyrogram String Session for protocol I/O only |
| `WORKER_CONTROL_GROUP_ID` | Telegram group ID — all bots must be members |
| `WORKER_OUTPUT_CHANNEL_ID` | Channel where workers upload completed files |
| `MONGO_URI` | MongoDB connection URI |
| `DB_NAME` | Database name (default: `DistributedRenameBot`) |
| `OWNER_IDS` | Space or comma-separated Telegram user IDs |

### Optional

| Variable | Default | Description |
|---|---|---|
| `CENTRAL_DUMP_CHANNEL_ID` | *(set in config)* | Channel for rich file info logs |
| `LOG_CHANNEL` | *(set in config)* | Channel for new user / startup events |
| `WORKER_OFFLINE_TIMEOUT` | `120` | Seconds before a worker is marked offline |
| `JOB_LEASE_SECONDS` | `300` | Seconds before an assigned job is re-queued |
| `IMGBB_API_KEY` | *(empty)* | ImgBB key for thumbnail sharing |
| `PORT` | `8000` | Health-check HTTP port |
| `BOT_MAX_SIZE` | `2000000000` | Max file size in bytes (2 GB) |
| `START_PIC` | *(URL)* | Image shown on /start |
| `SETTINGS_IMAGE` | *(URL)* | Image shown on settings menu |

---

## Generating a String Session

The String Session is used **only** for posting/reading protocol messages in the Worker Control Group — never for file downloads or uploads.

```bash
python -c "
from pyrogram import Client
client = Client('session', api_id=YOUR_API_ID, api_hash='YOUR_API_HASH')
client.run(client.export_session_string())
"
```

Copy the printed string into `STRING_SESSION`.

---

## Deployment

### Heroku

```bash
heroku create your-manager-bot
heroku config:set \
  API_ID=... \
  API_HASH=... \
  BOT_TOKEN=... \
  STRING_SESSION=... \
  WORKER_CONTROL_GROUP_ID=... \
  WORKER_OUTPUT_CHANNEL_ID=... \
  MONGO_URI=... \
  OWNER_IDS="123456 789012"
git push heroku main
```

The `Procfile` runs: `web: python manager_bot.py`

### Render / Koyeb

1. Push repo to GitHub.
2. Create a new **Web Service** (Python environment).
3. Set all required environment variables in the dashboard.
4. Set start command to `python manager_bot.py`.
5. The `/health` endpoint keeps the service alive.

### Local Development

```bash
git clone <repo>
cd manager-bot-main
cp .env.example .env
# Fill in .env
pip install -r requirements.txt
python manager_bot.py
```

---

## MongoDB Collections

| Collection | Description |
|---|---|
| `users` | Per-user settings (thumbnail, caption, prefix, suffix, metadata, premium, rename mode) |
| `jobs` | One document per file — full lifecycle with atomic state transitions |
| `batches` | One per `/autorename` … `/done` cycle |
| `workers` | Registered worker bots and live state |
| `global_settings` | Owner-controlled settings (global metadata, etc.) |
| `statistics` | Aggregate counters |
| `deliveries` | Delivery records for idempotency |

---

## User Commands

| Command | Description |
|---|---|
| `/start` | Start the bot |
| `/autorename` | Begin a batch rename session |
| `/done` | End batch rename session |
| `/setthumb` | Set custom thumbnail |
| `/delthumb` | Delete custom thumbnail |
| `/viewthumb` | Preview current thumbnail |
| `/setcaption` | Set file caption |
| `/setprefix` / `/setsuffix` | Set rename prefix/suffix |
| `/metadata` | Configure metadata fields |
| `/mediainfo` | Show file media info |
| `/leaderboard` | View rename stats |
| `/settings` | Open settings menu |

---

## License

See `Copyright.txt`. Project by [@naruto0927](https://t.me/naruto0927).
