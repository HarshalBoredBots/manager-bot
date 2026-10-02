"""
manager/plugins/template_engine.py
══════════════════════════════════════════════════════════════════════════════
Pure Python template rendering engine — no Pyrogram, no DB imports.

Extracted verbatim from plugins/auto_rename_engine.py.
Used by manager/plugins/batch.py to resolve filenames before queuing.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re

# ── Quality indicator patterns ────────────────────────────────────────────────
_QUAL_INDS = [
    r'\d{2,4}[pP]', r'\dK', r'HD(?:RIP)?', r'WEB(?:-)?DL', r'BLURAY',
    r'X264', r'X265', r'HEVC', r'FHD', r'UHD', r'HDR', r'H\.264', r'H\.265',
    r'(?:19|20)\d{2}', r'Multi(?:audio)?', r'Dual(?:audio)?',
]
_QPAT = r'(?:' + '|'.join(r'(?:[\s._-]*' + q + r')' for q in _QUAL_INDS) + r')'
_SKIP = {360, 480, 720, 1080, 1440, 2160, 2020, 2021, 2022, 2023, 2024, 2025}


def _strip_version_suffix(s: str) -> str:
    return re.sub(r'(\d+)\s*[vV]\d+', r'\1', s)


def _extract_episode_number(text: str):
    if not text:
        return None

    explicit = [
        re.compile(r'S(\d+)E(\d+)(?:\s*[vV]\d+)?',                   re.IGNORECASE),
        re.compile(r'S(\d+)\s+E(\d+)(?:\s*[vV]\d+)?',                re.IGNORECASE),
        re.compile(r'S(\d+)[._-]E(\d+)(?:\s*[vV]\d+)?',              re.IGNORECASE),
        re.compile(r'S(\d+)\s*-\s*E(\d+)(?:\s*[vV]\d+)?',           re.IGNORECASE),
        re.compile(r'(?<![A-Za-z\d])(\d+)E(\d+)(?:\s*[vV]\d+)?(?!\d)', re.IGNORECASE),
        re.compile(r'(?<![A-Za-z\d])(\d+)\s*[-_.]?\s*E(\d+)(?:\s*[vV]\d+)?(?!\d)', re.IGNORECASE),
    ]
    for pat in explicit:
        for m in pat.findall(text):
            raw = m[1] if isinstance(m, tuple) and len(m) >= 2 else m
            try:
                n = int(raw)
                if 1 <= n <= 9999 and n not in _SKIP:
                    return n
            except ValueError:
                pass

    season_bare = [
        re.compile(r'S\d+\s*-\s*(\d+)(?:\s*[vV]\d+)?',              re.IGNORECASE),
        re.compile(r'S\d+[._]+(\d+)(?:\s*[vV]\d+)?',                  re.IGNORECASE),
    ]
    for pat in season_bare:
        for m in pat.findall(text):
            raw = m[0] if isinstance(m, tuple) else m
            try:
                n = int(raw)
                if 1 <= n <= 9999 and n not in _SKIP:
                    return n
            except ValueError:
                pass

    keyword = [
        re.compile(r'\bEpisode\s+(\d+)(?:\s*[vV]\d+)?',              re.IGNORECASE),
        re.compile(r'\bEP\s*(\d+)(?:\s*[vV]\d+)?\b',                 re.IGNORECASE),
    ]
    for pat in keyword:
        for m in pat.findall(text):
            raw = m[0] if isinstance(m, tuple) else m
            try:
                n = int(raw)
                if 1 <= n <= 9999 and n not in _SKIP:
                    return n
            except ValueError:
                pass

    standalone_e = [
        re.compile(r'(?<![A-Za-z\d])E(\d+)(?:\s*[vV]\d+)?(?!\d)',   re.IGNORECASE),
        re.compile(r'[\[\(]E(\d+)(?:\s*[vV]\d+)?[\]\)]',             re.IGNORECASE),
    ]
    for pat in standalone_e:
        for m in pat.findall(text):
            raw = m[0] if isinstance(m, tuple) else m
            try:
                n = int(raw)
                if 1 <= n <= 9999 and n not in _SKIP:
                    return n
            except ValueError:
                pass

    m = re.search(r'\b(\d+)\s*of\s*\d+\b', text, re.IGNORECASE)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 9999 and n not in _SKIP:
                return n
        except ValueError:
            pass

    cleaned  = _strip_version_suffix(text)
    fallback = re.compile(
        r'(?:^|[^0-9A-Za-z])(\d{1,4})(?:[^0-9A-Za-z]|$)(?!' + _QPAT + r')',
        re.IGNORECASE,
    )
    for m in fallback.findall(cleaned):
        raw = m[0] if isinstance(m, tuple) else m
        try:
            n = int(raw)
            if 1 <= n <= 9999 and n not in _SKIP:
                return n
        except ValueError:
            pass

    return None


def _extract_season_number(text: str):
    if not text:
        return None
    patterns = [
        re.compile(r'S(\d+)[._-]?E\d+',                      re.IGNORECASE),
        re.compile(r'(?:Season|SEASON|season)[\s._-]*(\d+)', re.IGNORECASE),
        re.compile(r'\bS(\d+)\b(?!E\d|' + _QPAT + r')',     re.IGNORECASE),
        re.compile(r'[\[\(]S(\d+)[\]\)]',                    re.IGNORECASE),
        re.compile(r'[._-]S(\d+)(?:[._-]|$)',                re.IGNORECASE),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                n = int(m.group(1))
                if 1 <= n <= 99:
                    return n
            except ValueError:
                pass
    return None


def _extract_audio_info(text: str):
    kw = {
        'Hindi': r'Hindi', 'English': r'English', 'Multi': r'Multi(?:audio)?',
        'Telugu': r'Telugu', 'Tamil': r'Tamil', 'Jap': r'Jap',
        'Dual': r'Dual(?:audio)?', 'AAC': r'AAC', 'AC3': r'AC3',
        'DTS': r'DTS', '5.1': r'5\.1',
    }
    found = [k for k, p in kw.items() if re.search(p, text, re.IGNORECASE)]
    return ' '.join(found) if found else None


def _extract_quality(text: str):
    for pat in [
        re.compile(r'\b(4K|2K|2160p|1440p|1080p|720p|480p|360p)\b', re.IGNORECASE),
        re.compile(r'\b(HD(?:RIP)?|WEB(?:-)?DL|BLURAY)\b',           re.IGNORECASE),
        re.compile(r'\b(X264|X265|HEVC)\b',                           re.IGNORECASE),
    ]:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _apply_template(fmt: str, extraction_text: str, file_name: str) -> str:
    """Fill fmt placeholders using data extracted from extraction_text; keep file_name extension."""
    ep  = _extract_episode_number(extraction_text)
    s   = _extract_season_number(extraction_text)
    aud = _extract_audio_info(extraction_text)
    q   = _extract_quality(extraction_text)

    sfmt = str(s)            if s  is not None else "1"
    efmt = str(ep).zfill(2) if ep is not None else "01"

    t = fmt
    t = re.sub(r'S(?:Season|season|SEASON)(\d+)', f'S{sfmt}', t, flags=re.IGNORECASE)
    for pat in [re.compile(r'\{season\}', re.IGNORECASE),
                re.compile(r'\bseason\b',  re.IGNORECASE),
                re.compile(r'Season[\s._-]*\d*', re.IGNORECASE)]:
        t = pat.sub(sfmt, t)

    t = re.sub(r'EP(?:Episode|episode|EPISODE)', f'EP{efmt}', t, flags=re.IGNORECASE)
    for pat in [re.compile(r'\{episode\}', re.IGNORECASE),
                re.compile(r'\bEpisode\b',  re.IGNORECASE),
                re.compile(r'\bEP\b',       re.IGNORECASE)]:
        t = pat.sub(efmt, t)

    ar = aud or ""
    for pat in [re.compile(r'\{audio\}',   re.IGNORECASE),
                re.compile(r'\bAudio\b',   re.IGNORECASE)]:
        t = pat.sub(ar, t)

    qr = q or ""
    for pat in [re.compile(r'\{quality\}', re.IGNORECASE),
                re.compile(r'\bQuality\b', re.IGNORECASE)]:
        t = pat.sub(qr, t)

    t = re.sub(r'\[\s*\]', '', t)
    t = re.sub(r'\(\s*\)', '', t)
    t = re.sub(r'\{\s*\}', '', t)
    t = t.strip()

    _, ext = os.path.splitext(file_name)
    if ext and not t.lower().endswith(ext.lower()):
        t = f"{t}{ext}"
    return t
