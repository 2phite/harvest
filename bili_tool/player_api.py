"""Direct bilibili player-API subtitle fetch (cookie-authenticated, no WBI signing).

yt-dlp (even latest, 2026.06.09) does **not** surface bilibili's AI subtitle tracks. But the
plain `x/player/v2` endpoint returns them with the same login cookies yt-dlp already reads from
the browser — no WBI signing needed (the SPEC §3 surface we deliberately avoid). This is the
fallback that lights up the subtitle-reuse path (SPEC §5 step 2, D4) on real content when
`_pick_track` (yt-dlp's list) comes back empty.

Tracks carry `ai_type`: 0 = original-language transcription, 1 = a translation to another locale.
We only want the original zh transcription; translations are ignored.
"""

from __future__ import annotations

import json
import urllib.request

import yt_dlp

from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment
from .subtitles import _ZH_KEYS, parse_bcc, ydl_opts

_API_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
_API_PLAYER = "https://api.bilibili.com/x/player/v2?aid={aid}&cid={cid}&bvid={bvid}"
_UA = "Mozilla/5.0"


def cid_for_part(view_data: dict, part: int) -> int | None:
    """Map a 1-based part to its cid from web-interface/view `pages` (page-number match first,
    then positional index), falling back to the top-level cid for a single-part video."""
    pages = view_data.get("pages") or []
    for pg in pages:
        if pg.get("page") == part:
            return pg.get("cid")
    if 1 <= part <= len(pages):
        return pages[part - 1].get("cid")
    if part == 1:
        return view_data.get("cid")
    return None


def _zh_rank(lan: str) -> int:
    return _ZH_KEYS.index(lan) if lan in _ZH_KEYS else len(_ZH_KEYS)


def select_zh_subtitle(subtitles: list[dict]) -> dict | None:
    """Pick the original-language zh track: original transcription (ai_type 0) before any
    translation, then our zh-key preference order. Returns None if no zh track is present."""
    zh = [s for s in subtitles if s.get("lan") in _ZH_KEYS]
    if not zh:
        return None
    zh.sort(key=lambda s: (s.get("ai_type", 0), _zh_rank(s.get("lan", ""))))
    return zh[0]


def _opener(settings: Settings):
    """urllib opener carrying the same browser/SESSDATA cookies yt-dlp uses, + the Referer
    bilibili's CDN requires."""
    with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
        jar = ydl.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("Referer", REFERER), ("User-Agent", _UA)]
    return op


def _get_json(opener, url: str) -> dict:
    return json.loads(opener.open(url, timeout=60).read().decode("utf-8"))


def part_segments(
    canonical: Canonical, settings: Settings, *, opener=None
) -> tuple[str, list[Segment]] | None:
    """Fetch + parse the original-zh subtitle for this part via the player API.

    Returns (lang, segments) or None when no usable zh track exists. `opener` is injectable for
    tests; production builds one carrying the live cookies.
    """
    op = opener or _opener(settings)

    view = _get_json(op, _API_VIEW.format(bvid=canonical.id))
    if view.get("code") != 0:
        return None
    vdata = view.get("data") or {}
    aid = vdata.get("aid")
    cid = cid_for_part(vdata, canonical.part)
    if not (aid and cid):
        return None

    player = _get_json(op, _API_PLAYER.format(aid=aid, cid=cid, bvid=canonical.id))
    subs = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles")) or []
    pick = select_zh_subtitle(subs)
    if not pick:
        return None

    url = pick.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return None
    raw = op.open(url, timeout=60).read().decode("utf-8")
    segments = parse_bcc(raw)
    if not segments:
        return None
    return (pick.get("lan") or "ai-zh"), segments
