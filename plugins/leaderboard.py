"""
manager/plugins/leaderboard.py
══════════════════════════════════════════════════════════════════════════════
/leaderboard — rename stats with Today / Weekly / Monthly / All-Time filters.
/history     — last 20 renamed files per user.

Adapted from original — uses get_db() directly; rename_stats and
rename_history are separate collections on the same DB.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from helper.database import get_db
from helper.utils import humanbytes

_PAGE = 10

_PERIODS = {
    "today":   "Today",
    "weekly":  "Weekly",
    "monthly": "Monthly",
    "alltime": "All Time",
}

_HEADERS = {
    "today":   "Today",
    "weekly":  "This Week",
    "monthly": "This Month",
    "alltime": "All Time",
}


def _keys() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "day":   now.strftime("%Y-%m-%d"),
        "week":  now.strftime("%Y-W%W"),
        "month": now.strftime("%Y-%m"),
    }


async def record_rename(user_id: int, display_name: str, filename: str, filesize: int) -> None:
    """Call this after every successful job delivery to update stats + history."""
    db  = get_db()
    k   = _keys()
    col = db._db["rename_stats"]
    await col.update_one(
        {"_id": int(user_id)},
        {
            "$set":  {"name": display_name},
            "$inc":  {
                f"daily.{k['day']}":     1,
                f"weekly.{k['week']}":   1,
                f"monthly.{k['month']}": 1,
                "total":                 1,
            },
        },
        upsert=True,
    )
    history_col = db._db["rename_history"]
    entry = {
        "name": filename,
        "size": filesize,
        "ts":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    await history_col.update_one(
        {"_id": int(user_id)},
        {"$push": {"entries": {"$each": [entry], "$position": 0, "$slice": 20}}},
        upsert=True,
    )


async def _fetch(period: str, page: int = 0) -> tuple[list[dict], int]:
    db  = get_db()
    col = db._db["rename_stats"]
    k   = _keys()
    field = {
        "today":   f"daily.{k['day']}",
        "weekly":  f"weekly.{k['week']}",
        "monthly": f"monthly.{k['month']}",
    }.get(period, "total")

    pipeline = [
        {"$project": {"name": 1, "count": {"$ifNull": [f"${field}", 0]}}},
        {"$match":   {"count": {"$gt": 0}}},
        {"$sort":    {"count": -1}},
    ]
    all_docs = await col.aggregate(pipeline).to_list(None)
    return all_docs[page * _PAGE:(page + 1) * _PAGE], len(all_docs)


def _lb_text(entries: list, period: str, page: int, total: int) -> str:
    header = f"╭━━━〔 🏆 LEADERBOARD 〕━━━╮\n┃  ⚡  Period  ·  {_HEADERS.get(period, period)}"
    if not entries:
        return f"{header}\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n<i>No renames recorded yet.</i>"

    lines  = [header, "┣━━━━━━━━━━━━━━━━━━━━━━━━━"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    offset = page * _PAGE

    for i, e in enumerate(entries):
        rank   = offset + i + 1
        prefix = medals.get(rank, f"<code>{rank:>2}</code>")
        name   = (e.get("name") or "Unknown")[:22]
        count  = e["count"]
        lines.append(f"┃  {prefix}  {name}  ·  <b>{count:,}</b>")

    pages = max(1, (total + _PAGE - 1) // _PAGE)
    lines += ["╰━━━━━━━━━━━━━━━━━━━━━━━━╯", f"<i>Page  ·  {page+1}/{pages}</i>"]
    return "\n".join(lines)


def _lb_markup(period: str, page: int, total: int) -> InlineKeyboardMarkup:
    pages = max(1, (total + _PAGE - 1) // _PAGE)
    rows  = []
    row   = []
    for p, label in _PERIODS.items():
        mark = "· " if p == period else ""
        row.append(InlineKeyboardButton(f"{mark}{label}", callback_data=f"lb_{p}_0"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"lb_{period}_{page-1}"))
        nav.append(InlineKeyboardButton(f"📑 {page+1}/{pages}", callback_data="lb_noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"lb_{period}_{page+1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("✕ Close", callback_data="lb_close")])
    return InlineKeyboardMarkup(rows)


@Client.on_message(filters.private & filters.command("leaderboard"))
async def cmd_leaderboard(client: Client, message: Message) -> None:
    entries, total = await _fetch("today")
    await message.reply_text(
        _lb_text(entries, "today", 0, total),
        reply_markup=_lb_markup("today", 0, total),
    )


@Client.on_callback_query(filters.regex(r"^lb_"))
async def cb_leaderboard(client: Client, query: CallbackQuery) -> None:
    data = query.data
    if data == "lb_noop":
        return await query.answer()
    if data == "lb_close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    parts = data.split("_")
    if len(parts) != 3:
        return await query.answer()

    period = parts[1]
    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    if period not in _PERIODS:
        return await query.answer()

    await query.answer()
    entries, total = await _fetch(period, page)
    try:
        await query.message.edit_text(
            _lb_text(entries, period, page, total),
            reply_markup=_lb_markup(period, page, total),
        )
    except Exception:
        pass


# ── /history ──────────────────────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command(["history", "h"]))
async def cmd_history(client: Client, message: Message) -> None:
    db  = get_db()
    col = db._db["rename_history"]
    doc = await col.find_one({"_id": int(message.from_user.id)})
    entries = (doc or {}).get("entries", [])

    if not entries:
        text = (
            "╭━━━〔 📂 HISTORY 〕━━━╮\n"
            "┃  ⚠️  No history yet.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
    else:
        lines = ["╭━━━〔 📂 HISTORY 〕━━━╮"]
        for i, e in enumerate(entries, 1):
            name = e.get("name", "Unknown")
            size = humanbytes(e.get("size", 0))
            ts   = e.get("ts", "")[:16].replace("T", "  ")
            lines.append(f"<b>{i}.</b>  <code>{name}</code>")
            lines.append(f"      📦 {size}  ·  🕒 <i>{ts}</i>\n")
        text = "\n".join(lines).rstrip()

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Clear",   callback_data="hist_clear"),
            InlineKeyboardButton("✕ Dismiss", callback_data="hist_close"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^hist_(clear|close)$"))
async def cb_history(client: Client, query: CallbackQuery) -> None:
    action = query.data.split("_")[1]
    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    db  = get_db()
    col = db._db["rename_history"]
    await col.delete_one({"_id": int(query.from_user.id)})
    await query.answer("History cleared.")
    try:
        await query.message.edit_text(
            "╭━━━〔 📂 HISTORY 〕━━━╮\n┃  ✅  History cleared.\n╰━━━━━━━━━━━━━━━━━━━━━━━━╯",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✕ Dismiss", callback_data="hist_close")
            ]]),
        )
    except Exception:
        pass
