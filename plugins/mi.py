"""
manager/plugins/mi.py
══════════════════════════════════════════════════════════════════════════════
/mi command — reply to any media file for full MediaInfo.
Adapted from original plugins/mediainfo.py — jishubotz → get_db().
All pure functions (_ffprobe_sync, _build_telegraph_nodes, etc.) are
imported from a shared module to avoid duplication.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from helper.database import get_db

logger = logging.getLogger(__name__)

TEMP_DIR = "downloads/mediainfo"

_telegraph_token: str | None = None


@Client.on_message(filters.private & filters.command("mi"))
async def mediainfo_cmd(client: Client, message: Message) -> None:
    db    = get_db()
    reply = message.reply_to_message
    if not reply or not reply.media:
        return await message.reply_text(
            "❌ <b>Reply to a media file</b> with /mi to get its MediaInfo."
        )

    media = getattr(reply, reply.media.value, None)
    if media is None:
        return await message.reply_text("❌ Unsupported media type.")

    if await db.is_banned(message.from_user.id):
        return

    status    = await message.reply_text("⏳ Fetching MediaInfo…")
    file_path = None

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        raw_name  = getattr(media, "file_name", None) or f"mi_{int(time.time())}"
        file_size = getattr(media, "file_size", 0) or 0
        safe_name = "".join(c for c in raw_name if c.isalnum() or c in "._- []@")
        file_path = os.path.join(
            TEMP_DIR,
            f"{message.from_user.id}_{int(time.time())}_{safe_name}",
        )

        partial_limit = min(int(file_size * 0.15), 50 * 1024 * 1024)
        partial_limit = max(partial_limit, 2 * 1024 * 1024)

        await status.edit(
            f"⏳ Downloading header ({_humanbytes(partial_limit)} of "
            f"{_humanbytes(file_size)})…"
        )

        file_path = await _partial_download(client, reply, file_path, partial_limit)
        if not file_path or not os.path.exists(file_path):
            return await status.edit("❌ Failed to fetch file header.")

        await status.edit(f"🔍 Analysing streams…")

        from helper.ffmpeg import run_blocking
        data = await run_blocking(_ffprobe_sync, file_path)

        await status.edit("📤 Uploading to Telegraph…")

        bot_username = client.username or "ManagerBot"
        nodes        = _build_telegraph_nodes(data, raw_name, file_size, bot_username)
        page_url     = await _upload_to_telegraph(
            f"MediaInfo of {raw_name}", nodes, bot_username
        )

        if page_url:
            await status.edit(
                f"📊 <b>MediaInfo</b>\n\n"
                f"<b>File:</b> <code>{raw_name}</code>\n"
                f"<b>Size:</b> <code>{_humanbytes(file_size)}</code>\n\n"
                f"🔗 {page_url}",
            )
        else:
            plain     = _build_plain_fallback(data, raw_name, file_size)
            truncated = plain[:3800] + ("\n\n… (truncated)" if len(plain) > 3800 else "")
            await status.edit(f"📊 <b>MediaInfo</b>\n\n<code>{truncated}</code>")

    except Exception as exc:
        logger.error("[mi] Error: %s", exc)
        await status.edit(f"❌ Failed to generate MediaInfo.\n\n<code>{exc}</code>")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


async def _partial_download(client, message, dest_path, limit_bytes):
    try:
        written = 0
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        with open(dest_path, "wb") as f:
            async for chunk in client.stream_media(message, limit=limit_bytes):
                f.write(chunk)
                written += len(chunk)
                if written >= limit_bytes:
                    break
        return dest_path if written > 0 else None
    except Exception:
        try:
            return await client.download_media(message, file_name=dest_path)
        except Exception:
            return None


def _ffprobe_sync(file_path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-probesize", "50000000", "-analyzeduration", "10000000",
        "-print_format", "json", "-show_format", "-show_streams", "-show_chapters",
        file_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.stdout:
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


# Reuse helpers from worker's mediainfo module to avoid duplication
def _build_telegraph_nodes(data, display_name, file_size, bot_username):
    # Import from worker plugin is not available here — use inline copy
    # (same as worker/plugins/mediainfo.py — kept in sync)
    from datetime import datetime

    fmt = data.get("format", {}); streams = data.get("streams", [])
    chapters = data.get("chapters", []); tags_f = fmt.get("tags", {})

    def h3(t):      return {"tag": "h3", "children": [t]}
    def h4(t):      return {"tag": "h4", "children": [t]}
    def p(*c):      return {"tag": "p",  "children": list(c)}
    def bold(t):    return {"tag": "b",  "children": [t]}
    def em(t):      return {"tag": "em", "children": [t]}
    def code(t):    return {"tag": "code", "children": [t]}
    def br():       return {"tag": "br"}
    def hr():       return {"tag": "hr"}
    def link(u, t): return {"tag": "a", "attrs": {"href": u}, "children": [t]}
    def li(*c):     return {"tag": "li", "children": list(c)}
    def ol(items):  return {"tag": "ol", "children": items}

    nodes = []
    nodes.append(p(link(f"https://t.me/{bot_username}", f"@{bot_username}"),
                   f"  {datetime.utcnow().strftime('%B %d, %Y')}"))
    nodes.append(h4(f"MediaInfo of {display_name}")); nodes.append(hr())
    nodes.append(h3("🎬 General Info"))

    fmt_name  = fmt.get("format_long_name") or fmt.get("format_name") or "N/A"
    dur       = float(fmt.get("duration") or 0)
    br_raw    = fmt.get("bit_rate", "")
    title_tag = tags_f.get("title") or tags_f.get("TITLE") or ""
    res_str   = ""
    for s in streams:
        if s.get("codec_type") == "video":
            w = s.get("width"); h_ = s.get("height")
            if w and h_: res_str = f"{w}x{h_}"
            break
    if title_tag: nodes.append(p(bold("Title: "), title_tag))
    nodes.append(p(bold("Format: "), fmt_name))
    if res_str:   nodes.append(p(bold("Resolution: "), res_str))
    if dur:       nodes.append(p(bold("Duration: "), _fmt_dur_long(int(dur))))
    nodes.append(p(bold("File Size: "), _humanbytes(file_size)))
    if br_raw:    nodes.append(p(bold("Bitrate: "), _fmt_br(br_raw)))
    nodes.append(hr())

    for s in [s for s in streams if s.get("codec_type") == "video"][:1]:
        nodes.append(h3("🖼️ Video Stream"))
        stags = s.get("tags", {})
        cl = s.get("codec_long_name", ""); cn = s.get("codec_name", "?")
        cs = (cl + f" ({cn})") if cl else cn
        pr = s.get("profile", "")
        if pr and pr not in ("unknown", ""): cs += f" - {pr}"
        fps = _parse_fps(s.get("r_frame_rate", "")) or _parse_fps(s.get("avg_frame_rate", "")) or ""
        dar = s.get("display_aspect_ratio", "")
        if stags.get("title"): nodes.append(p(bold("Title: "), stags["title"]))
        nodes.append(p(bold("Codec: "), cs))
        if s.get("pix_fmt"): nodes.append(p(bold("Pixel Format: "), s["pix_fmt"]))
        c_str = ", ".join(filter(None, [s.get("color_space",""), s.get("color_primaries","")]))
        if c_str: nodes.append(p(bold("Color: "), c_str))
        if dar and dar != "0:1": nodes.append(p(bold("Aspect Ratio: "), dar))
        if fps: nodes.append(p(bold("Frame Rate: "), fps))
        nodes.append(hr())

    audio_s = [s for s in streams if s.get("codec_type") == "audio"]
    if audio_s:
        nodes.append(h3("🔊 Audio Tracks")); items = []
        for s in audio_s:
            st = s.get("tags", {}); lang = _lang_display(st.get("language") or st.get("LANGUAGE") or "")
            d = s.get("disposition", {})
            flags = (["Default"] if d.get("default") else []) + (["Forced"] if d.get("forced") else [])
            fl = f" ({', '.join(flags)})" if flags else ""
            cl = s.get("codec_long_name",""); cn = s.get("codec_name","?")
            cs = (cl + f" ({cn})") if cl else cn
            ch = s.get("channel_layout","") or (f"{s.get('channels','')}ch" if s.get("channels") else "")
            abr = s.get("bit_rate",""); sr = s.get("sample_rate","")
            sr_s = f"{int(float(sr))//1000}kHz" if sr else ""
            dp = [cs] + ([ch] if ch else []) + ([f"@ {_fmt_br(abr)}"] if abr else []) + ([sr_s] if sr_s else [])
            ic = [bold(f"{lang}{fl} - "), " ".join(dp)]
            at = st.get("title") or st.get("TITLE") or ""
            if at: ic += [br(), em(f"  ‣ {at}")]
            items.append(li(*ic))
        nodes.append(ol(items)); nodes.append(hr())

    sub_s = [s for s in streams if s.get("codec_type") == "subtitle"]
    if sub_s:
        nodes.append(h3("📝 Subtitle Tracks")); items = []
        for s in sub_s:
            st = s.get("tags", {}); lang = _lang_display(st.get("language") or st.get("LANGUAGE") or "")
            d = s.get("disposition", {})
            flags = (["Default"] if d.get("default") else []) + (["Forced"] if d.get("forced") else [])
            fl = f" ({', '.join(flags)})" if flags else ""
            cl = s.get("codec_long_name",""); cn = s.get("codec_name","?")
            cs = (cl + f" ({cn})") if cl else cn
            st_t = st.get("title") or st.get("TITLE") or ""
            ic = [bold(f"{lang}{fl} - "), em(cs)]
            if st_t: ic += [br(), f"  ‣ {st_t}"]
            items.append(li(*ic))
        nodes.append(ol(items)); nodes.append(hr())

    if chapters:
        nodes.append(h3("🔖 Chapters")); ci = []
        for i, ch in enumerate(chapters, 1):
            ct = ch.get("tags", {}); tt = ct.get("title") or ct.get("TITLE") or f"Chapter {i}"
            ci.append(li(code(f"{tt}:"), f" {_fmt_timestamp(float(ch.get('start_time',0)))} - {_fmt_timestamp(float(ch.get('end_time',0)))}"))
        nodes.append(ol(ci)); nodes.append(hr())

    tech = []
    wa = tags_f.get("writing_application") or tags_f.get("WRITING_APPLICATION") or tags_f.get("encoder") or tags_f.get("ENCODER") or ""
    eb = tags_f.get("encoded_by") or tags_f.get("ENCODED_BY") or ""
    nb = fmt.get("nb_streams", "")
    if wa: tech.append(p(bold("Muxed with: "), wa))
    if eb: tech.append(p(bold("Encoded By: "), eb))
    if nb: tech.append(p(bold("Total Streams: "), str(nb)))
    if tech: nodes.append(h3("🛠️ Technical")); nodes.extend(tech)
    return nodes


def _build_plain_fallback(data, display_name, file_size):
    fmt = data.get("format", {}); streams = data.get("streams", [])
    lines = [f"━━ GENERAL ━━", f"  Name     : {display_name}", f"  Size     : {_humanbytes(file_size)}",
             f"  Format   : {fmt.get('format_long_name') or fmt.get('format_name') or 'N/A'}"]
    dur = float(fmt.get("duration") or 0)
    if dur: lines.append(f"  Duration : {_fmt_dur_long(int(dur))}")
    br = fmt.get("bit_rate","")
    if br: lines.append(f"  Bitrate  : {_fmt_br(br)}")
    lines.append("")
    for s in streams:
        ct = (s.get("codec_type") or "").upper(); st = s.get("tags", {})
        lang = st.get("language") or st.get("LANGUAGE") or ""; codec = s.get("codec_name","?")
        if ct == "VIDEO":
            lines += ["━━ VIDEO ━━", f"  Codec : {codec}"]
            w = s.get("width"); h_ = s.get("height")
            if w and h_: lines.append(f"  Res   : {w}x{h_}")
            fps = _parse_fps(s.get("r_frame_rate",""))
            if fps: lines.append(f"  FPS   : {fps}")
            lines.append("")
        elif ct == "AUDIO":
            ld = _lang_display(lang) if lang else "Unknown"
            d = s.get("disposition",{}); fl = []
            if d.get("default"): fl.append("Default")
            if d.get("forced"):  fl.append("Forced")
            abr = s.get("bit_rate","")
            lines.append(f"  🔊 {ld}{(' (' + ', '.join(fl) + ')') if fl else ''} — {codec}" + (f" @ {_fmt_br(abr)}" if abr else ""))
        elif ct == "SUBTITLE":
            ld = _lang_display(lang) if lang else "Unknown"
            d = s.get("disposition",{}); fl = []
            if d.get("default"): fl.append("Default")
            if d.get("forced"):  fl.append("Forced")
            lines.append(f"  📝 {ld}{(' (' + ', '.join(fl) + ')') if fl else ''} — {codec}")
    return "\n".join(lines).strip()


async def _upload_to_telegraph(title, nodes, bot_username):
    global _telegraph_token
    _LIMIT = 60_000
    safe = list(nodes)
    while True:
        nj = json.dumps(safe, ensure_ascii=False)
        if len(nj.encode("utf-8")) <= _LIMIT: break
        if not safe: return None
        safe.pop()
    if not _telegraph_token:
        for a in range(3):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                    async with s.post("https://api.telegra.ph/createAccount", data={
                        "short_name": bot_username[:32], "author_name": f"@{bot_username}",
                        "author_url": f"https://t.me/{bot_username}",
                    }) as r:
                        d = json.loads(await r.text())
                        if d.get("ok"): _telegraph_token = d["result"]["access_token"]; break
            except Exception: pass
            if a < 2: await asyncio.sleep(3)
    if not _telegraph_token: return None
    for a in range(3):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post("https://api.telegra.ph/createPage", data={
                    "access_token": _telegraph_token, "title": (title or "MediaInfo")[:256],
                    "author_name": f"@{bot_username}", "author_url": f"https://t.me/{bot_username}",
                    "content": nj,
                }) as r:
                    res = json.loads(await r.text())
                    if res.get("ok"): return f"https://telegra.ph/{res['result']['path']}"
                    if "ACCESS_TOKEN" in str(res.get("error","")).upper():
                        _telegraph_token = None; break
        except Exception: pass
        if a < 2: await asyncio.sleep(3)
    return None


_LANG_MAP = {
    "jpn":"Japanese","eng":"English","ger":"German","deu":"German","spa":"Castilian / Spanish",
    "fre":"French","fra":"French","ita":"Italian","por":"Portuguese","tha":"Thai","ara":"Arabic",
    "hin":"Hindi","chi":"Chinese","zho":"Chinese","kor":"Korean","rus":"Russian","tur":"Turkish",
    "pol":"Polish","dut":"Dutch / Flemish","nld":"Dutch / Flemish","ind":"Indonesian",
    "may":"Malay","msa":"Malay","vie":"Vietnamese","swe":"Swedish","nor":"Norwegian",
    "dan":"Danish","fin":"Finnish","heb":"Hebrew","ces":"Czech","cze":"Czech","slk":"Slovak",
    "hun":"Hungarian","ron":"Romanian","rum":"Romanian","bul":"Bulgarian","hrv":"Croatian",
    "srp":"Serbian","ukr":"Ukrainian","cat":"Catalan",
}
def _lang_display(code):
    return _LANG_MAP.get((code or "").lower().strip(), (code or "Unknown").title())
def _humanbytes(size):
    try: size = int(size)
    except: return "N/A"
    if size <= 0: return "N/A"
    for u in ("B","KB","MB","GB","TB"):
        if size < 1024: return f"{size:.2f} {u}"
        size /= 1024
    return f"{size:.2f} PB"
def _fmt_dur_long(s):
    h=s//3600;m=(s%3600)//60;s=s%60
    p=[]
    if h: p.append(f"{h}hr")
    if m: p.append(f"{m}mins")
    if s or not p: p.append(f"{s}s")
    return " ".join(p)
def _fmt_timestamp(s):
    t=int(s);h=t//3600;m=(t%3600)//60;s=t%60
    return f"{h}:{m:02d}:{s:02d}"
def _fmt_br(br):
    try:
        br=int(br)
        if br>=1_000_000: return f"{br/1_000_000:.2f} Mbps"
        if br>=1_000: return f"{br/1_000:.0f} kbps"
        return f"{br} bps"
    except: return str(br)
def _parse_fps(f):
    try:
        if "/" in f:
            n,d=f.split("/"); v=float(n)/float(d)
            if v<=0: return ""
            for k in (23.976,24.0,25.0,29.97,30.0,48.0,50.0,59.94,60.0,120.0):
                if abs(v-k)<0.01: return f"{k:.3f}".rstrip("0").rstrip(".")
            return f"{v:.3f}".rstrip("0").rstrip(".")
        v=float(f); return f"{v:.3f}".rstrip("0").rstrip(".") if v>0 else ""
    except: return ""
