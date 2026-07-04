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
import logging
import math
import urllib.request
import zlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.error import URLError

import yt_dlp
from pydantic import BaseModel, ValidationError

from .config import REFERER, Settings
from .danmaku_proto import RawDanmaku, decode_seg
from .resolve import Canonical
from .schema import Segment
from .subtitles import _ZH_KEYS, parse_bcc, ydl_opts

logger = logging.getLogger(__name__)

_API_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
_API_PLAYER = "https://api.bilibili.com/x/player/v2?aid={aid}&cid={cid}&bvid={bvid}"
_UA = "Mozilla/5.0"

# bilibili.com is a China-domestic platform; `pubdate` is presented in China Standard Time.
# Centralized here so a future scope expansion (bilibili.tv, YouTube) has one place to map
# per-platform source timezones instead of hunting through call sites.
SOURCE_TZ = timezone(timedelta(hours=8))  # CST / UTC+8


def published_at_iso(pubdate: int | None) -> str | None:
    """Convert a `pubdate` Unix-seconds epoch to an ISO 8601 string in `SOURCE_TZ`.

    `None`/`0` (bilibili's "unknown" sentinel) both map to `None`.
    """
    if not pubdate:
        return None
    return datetime.fromtimestamp(pubdate, tz=SOURCE_TZ).isoformat()


class ViewError(Exception):
    """Raised when web-interface/view responds with a non-zero `code`."""


class ViewPage(BaseModel):
    """One entry of a (possibly multi-part) video, from `data.pages[]` (or synthesized)."""

    part: int
    cid: int | None = None
    title: str | None = None
    duration: int | None = None


class ViewData(BaseModel):
    """Parsed `web-interface/view` response: the single source of truth for video metadata.

    `pic` + the `*_count` fields (Task 1) come from the SAME response's `data.pic`/`data.stat.*`
    -- no second network call.
    """

    aid: int | None = None
    cid: int | None = None
    title: str | None = None
    desc: str | None = None
    duration: int | None = None
    pubdate: int | None = None  # Unix seconds, publish time (SPEC: published_at source)
    owner_mid: int | None = None
    owner_name: str | None = None
    staff_mids: list[int] = []  # 合作 co-author mids from data.staff[]; [] when solo
    pic: str | None = None
    view_count: int | None = None
    danmaku_count: int | None = None
    like_count: int | None = None
    coin_count: int | None = None
    favorite_count: int | None = None
    share_count: int | None = None
    reply_count: int | None = None
    pages: list[ViewPage] = []


def cid_for_part(view_data: ViewData, part: int) -> int | None:
    """Map a 1-based part to its cid from `ViewData.pages` (page-number match first, then
    positional index), falling back to the top-level cid for a single-part video.
    """
    pages = view_data.pages
    for pg in pages:
        if pg.part == part:
            return pg.cid
    if 1 <= part <= len(pages):
        return pages[part - 1].cid
    if part == 1:
        return view_data.cid
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


def fetch_view(canonical: Canonical, settings: Settings, *, opener=None) -> ViewData:
    """Fetch + parse `web-interface/view` for this video: one GET, the single source of truth
    for title/owner/desc/duration/pages.

    Raises `ViewError` when the response's `code != 0`. `opener` is injectable for tests;
    production builds one carrying the live cookies.
    """
    op = opener or _opener(settings)
    view = _get_json(op, _API_VIEW.format(bvid=canonical.id))
    if view.get("code") != 0:
        raise ViewError(f"web-interface/view error: code={view.get('code')!r}, "
                         f"message={view.get('message')!r}")

    data = view.get("data") or {}
    owner = data.get("owner") or {}
    staff = data.get("staff") or []
    staff_mids = [s["mid"] for s in staff if isinstance(s, dict) and s.get("mid") is not None]
    desc = data.get("desc") or None
    stat = data.get("stat") or {}

    try:
        raw_pages = data.get("pages") or []
        if raw_pages:
            pages = [
                ViewPage(
                    part=pg.get("page"),
                    cid=pg.get("cid"),
                    title=pg.get("part"),
                    duration=pg.get("duration"),
                )
                for pg in raw_pages
            ]
        else:
            pages = [
                ViewPage(part=1, cid=data.get("cid"), title=None, duration=data.get("duration"))
            ]

        return ViewData(
            aid=data.get("aid"),
            cid=data.get("cid"),
            title=data.get("title"),
            desc=desc,
            duration=data.get("duration"),
            pubdate=data.get("pubdate"),
            owner_mid=owner.get("mid"),
            owner_name=owner.get("name"),
            staff_mids=staff_mids,
            pic=data.get("pic") or None,
            view_count=stat.get("view"),
            danmaku_count=stat.get("danmaku"),
            like_count=stat.get("like"),
            coin_count=stat.get("coin"),
            favorite_count=stat.get("favorite"),
            share_count=stat.get("share"),
            reply_count=stat.get("reply"),
            pages=pages,
        )
    except ValidationError as exc:
        raise ViewError(
            f"web-interface/view returned an unparseable shape: {exc}"
        ) from exc


def part_segments(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> tuple[str, list[Segment]] | None:
    """Fetch + parse the original-zh subtitle for this part via the player API.

    Returns (lang, segments) or None when no usable zh track exists. `opener` is injectable for
    tests; production builds one carrying the live cookies. `view` lets a caller that already
    fetched `ViewData` (Task 4: one fetch per part) share it instead of triggering a second GET.
    """
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            return None
    aid = view.aid
    cid = cid_for_part(view, canonical.part)
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
    # language key (provenance set to "auto-sub" by subtitles._acquire)
    return (pick.get("lan") or "ai-zh"), segments


# --- danmaku acquisition: the protobuf CENSUS endpoint (`x/v2/dm/web/seg.so`). Replaces the old
# server-sampled XML endpoint outright (see task brief) -- the census returns every danmaku per
# ~6-minute segment (`segment_index`), paged until a segment comes back empty. `RawDanmaku` /
# `decode_seg` live in `.danmaku_proto` (the dependency-free protobuf reader); this module owns
# only the HTTP pagination loop. ---

_API_DANMAKU_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={idx}"


@dataclass(frozen=True)
class DanmakuFetch:
    """Result of a danmaku acquisition attempt: `source_total` is bilibili's platform-reported
    count (`ViewData.danmaku_count`, may be `None` if unavailable), `fetched_total` is how many
    records this fetch actually got (`len(records)`). No `sampled` flag: the census's only gap
    vs. `source_total` is the small delete-before-fetch window, not a server-side sampling cap --
    `source_total` and `fetched_total` together already tell that story honestly."""

    source_total: int | None
    fetched_total: int
    records: list[RawDanmaku]


def _mid_crc32(mid: int) -> int:
    """bilibili's danmaku poster hash is the standard CRC32 of the mid's decimal string."""
    return zlib.crc32(str(mid).encode("utf-8")) & 0xFFFFFFFF


def classify_authors(
    records: list[RawDanmaku], owner_mid: int | None, staff_mids: list[int]
) -> list[RawDanmaku]:
    """Tag each record's `author` by crc32-matching its `mid_hash` against the video's author mids:
    `owner_mid` -> "owner" (UP主), any `staff_mids` entry -> "staff" (合作). Owner wins on overlap.
    Records that match no author, or whose `mid_hash` is empty/unparseable, keep author=None.

    Pure and deterministic, no network. Compares integers (crc32 value vs int(mid_hash, 16)) so it
    is robust to leading-zero padding / case in bilibili's hex rendering of the hash. Returns the
    input list object unchanged when there are no author mids to match against.
    """
    role_by_hash: dict[int, str] = {}
    for mid in staff_mids:
        if mid is not None:
            role_by_hash[_mid_crc32(mid)] = "staff"
    if owner_mid is not None:
        role_by_hash[_mid_crc32(owner_mid)] = "owner"  # owner precedence over any staff overlap
    if not role_by_hash:
        return records
    out: list[RawDanmaku] = []
    for r in records:
        role: str | None = None
        if r.mid_hash:
            try:
                role = role_by_hash.get(int(r.mid_hash, 16))
            except ValueError:
                role = None
        out.append(replace(r, author=role) if role else r)
    return out


def fetch_danmaku(
    canonical: Canonical, settings: Settings, *, opener=None, view: ViewData | None = None
) -> DanmakuFetch:
    """Fetch + parse the raw danmaku stream for this part via the census protobuf endpoint
    (`x/v2/dm/web/seg.so`), paging `segment_index` from 1 until a segment yields no danmaku
    (an empty body, or a non-protobuf JSON error body such as `{"code":-352}` -- both decode to
    `[]` via `decode_seg`, which is treated as "no more segments").

    Always returns a `DanmakuFetch`, never raises or returns `None`: when no cid resolves for
    the part (or the view itself is unavailable/`ViewError`s), returns an empty result --
    `records=[]`, `fetched_total=0`, `source_total` carried over from `view` when one was
    available. This mirrors `part_segments`'s "absence degrades gracefully" stance, adapted to a
    non-Optional return type since `DanmakuFetch` already has a natural empty state.

    A `URLError`/HTTP error partway through pagination is logged as a warning and the fetch
    returns what it gathered so far (partial) rather than raising or discarding it -- the low
    `fetched_total` tells the honest story. `source_total` always comes from `view.danmaku_count`
    (the `fetch_view` call already made), never from `seg.so` itself.

    `opener` is injectable for tests; production builds one carrying the live cookies. `view`
    lets a caller that already fetched `ViewData` (Task 4: one fetch per part) share it instead
    of triggering a second `web-interface/view` GET.
    """
    op = opener or _opener(settings)

    if view is None:
        try:
            view = fetch_view(canonical, settings, opener=op)
        except ViewError:
            view = None

    source_total = view.danmaku_count if view is not None else None
    cid = cid_for_part(view, canonical.part) if view is not None else None
    if not cid:
        return DanmakuFetch(source_total=source_total, fetched_total=0, records=[])

    expected_segments = (
        math.ceil(view.duration / 360) if view is not None and view.duration else None
    )

    records: list[RawDanmaku] = []
    idx = 1
    while True:
        url = _API_DANMAKU_SEG.format(cid=cid, idx=idx)
        try:
            body = op.open(url, timeout=60).read()
        except URLError as exc:
            if expected_segments is not None and idx > expected_segments:
                # bilibili returns 304/errors for the first segment past real content -- this is
                # the normal end-of-data terminator, not a truncation, so no warning.
                logger.debug(
                    "danmaku seg.so pagination ended at segment_index=%d for cid=%s "
                    "(past expected_segments=%d): %s",
                    idx, cid, expected_segments, exc,
                )
                break
            logger.warning(
                "danmaku seg.so pagination stopped early at segment_index=%d for cid=%s: %s",
                idx, cid, exc,
            )
            break
        seg_records = decode_seg(body)
        if not seg_records:
            break
        records.extend(seg_records)
        idx += 1

    records.sort(key=lambda r: r.content_ts)
    if view is not None:
        records = classify_authors(records, view.owner_mid, view.staff_mids)
    return DanmakuFetch(source_total=source_total, fetched_total=len(records), records=records)
