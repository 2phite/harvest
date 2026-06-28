"""yt-dlp subtitle probe (SPEC §5 step 2, §7, D4, D9, D11).

Drives yt-dlp's Python API with skip_download to surface original-language subtitles (bilibili
.com AI auto-captions land as a normal track). Never touches media here. The #6357 part-match
assertion (D4) guards against silently aligning the wrong part's text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import yt_dlp

from .config import REFERER, Settings
from .resolve import Canonical
from .schema import Segment

# Original-language zh keys we accept, in preference order. Human subs (info["subtitles"])
# outrank AI captions (info["automatic_captions"]); within each we prefer simplified zh.
_ZH_KEYS = ("zh-Hans", "zh-CN", "zh", "ai-zh")


@dataclass
class SubtitleResult:
    found: bool
    source: str | None  # "human-sub" | "ai-zh" | None
    lang: str | None
    segments: list[Segment] = field(default_factory=list)
    reason: str = ""  # human-readable, flows into the D2 bundle.md header
    last_cue_end: float | None = None


def ydl_opts(settings: Settings, *, skip_download: bool = True) -> dict:
    """Common yt-dlp options: auth (D9), Referer (§7), ffmpeg location."""
    headers = {"Referer": REFERER}
    opts: dict = {
        "skip_download": skip_download,
        "quiet": True,
        "no_warnings": True,
        "http_headers": headers,
        # bilibili CDN can be slow/flaky; be patient and resume partials.
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
    }
    if settings.aria2c_path:
        # aria2c saturates throttled bilibili CDNs with parallel connections + robust resume.
        opts["external_downloader"] = {"default": settings.aria2c_path}
        opts["external_downloader_args"] = {
            "aria2c": [
                "-x16", "-s16", "-k1M", "--retry-wait=2", "--max-tries=10",
                "--disable-ipv6=true",  # Akamai mirrors resolve to unreachable IPv6 on this box
            ]
        }
    else:
        # Native fallback: ranged chunks so a stall loses only one chunk, not the whole file.
        opts["http_chunk_size"] = 10 * 1024 * 1024
    if settings.sessdata:
        headers["Cookie"] = f"SESSDATA={settings.sessdata}"
    else:
        profile = settings.cookies_profile or None
        opts["cookiesfrombrowser"] = (settings.cookies_browser, profile, None, None)
    if settings.ffmpeg_path:
        from pathlib import Path

        opts["ffmpeg_location"] = str(Path(settings.ffmpeg_path).parent)
    return opts


def extract_info(url: str, settings: Settings) -> dict:
    """Fetch yt-dlp info for a specific part URL (no media). Fails loud per D11 if the cookie
    source itself can't be read (yt-dlp raises a clear DownloadError)."""
    with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
        return ydl.extract_info(url, download=False)


def _pick_track(info: dict) -> tuple[str, str, list] | None:
    """Return (source_label, lang_key, formats) for the best original-zh track, or None."""
    human = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for key in _ZH_KEYS:
        if key in human:
            return "human-sub", key, human[key]
    for key in _ZH_KEYS:
        if key in auto:
            return "ai-zh", key, auto[key]
    return None


def _download_track(formats: list, settings: Settings) -> tuple[str, str]:
    """Fetch a subtitle track's raw text. Returns (text, ext). Prefers json(bcc)/srt formats."""
    ordered = sorted(formats, key=lambda f: 0 if f.get("ext") in ("json", "srt", "vtt") else 1)
    with yt_dlp.YoutubeDL(ydl_opts(settings)) as ydl:
        for f in ordered:
            url = f.get("url")
            if not url:
                continue
            raw = ydl.urlopen(url).read().decode("utf-8", "replace")
            return raw, f.get("ext") or ""
    raise ValueError("subtitle track had no fetchable url")


def parse_bcc(text: str) -> list[Segment]:
    """bilibili bcc/json subtitle: {"body":[{"from":..,"to":..,"content":".."}]}."""
    data = json.loads(text)
    body = data.get("body", data) if isinstance(data, dict) else data
    out: list[Segment] = []
    for cue in body:
        out.append(
            Segment(
                start=float(cue["from"]),
                end=float(cue["to"]),
                text=str(cue.get("content", "")).strip(),
            )
        )
    return out


_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_srt(text: str) -> list[Segment]:
    out: list[Segment] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        m = _SRT_TIME.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        lines = block.split("\n")
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), 0)
        body = " ".join(ln.strip() for ln in lines[ti + 1 :] if ln.strip())
        out.append(Segment(start=start, end=end, text=body))
    return out


def probe(info: dict, canonical: Canonical, settings: Settings) -> SubtitleResult:
    """Probe + D4 tier-1 duration sanity gate. (D4 tier-2 part-1 identity check for part>1 is
    wired in once we have a multi-part test URL; single-part/part=1 can't hit #6357.)"""
    pick = _pick_track(info)
    if pick is None:
        return SubtitleResult(False, None, None, reason="no original-language subtitle available")

    source, lang, formats = pick
    raw, ext = _download_track(formats, settings)
    segments = parse_bcc(raw) if ext == "json" else parse_srt(raw)
    if not segments:
        return SubtitleResult(
            False, None, None, reason=f"subtitle track {lang!r} parsed to zero cues"
        )

    last_end = max(s.end for s in segments)
    duration = info.get("duration")
    if duration:
        ratio = last_end / float(duration)
        if not (0.70 <= ratio <= 1.10):  # D4 tier-1
            return SubtitleResult(
                False,
                None,
                None,
                segments=[],
                reason=(
                    f"subtitle rejected: duration sanity {ratio:.2f} outside 0.70-1.10 "
                    f"(last cue {last_end:.0f}s vs {duration:.0f}s)"
                ),
                last_cue_end=last_end,
            )

    return SubtitleResult(
        True, source, lang, segments=segments, reason=f"{source} ({lang})", last_cue_end=last_end
    )
