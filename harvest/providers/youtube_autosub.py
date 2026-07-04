"""YouTube auto-caption acquisition + structural validity net (SPEC §6).

Pure helpers, provider-orchestrated. The net is LANGUAGE-AGNOSTIC and structural (presence,
coverage, chars-per-second) — deliberately NOT the CJK `harvest/quality.py` gate, which can't be
calibrated across YouTube's ~150 language variants. Fail-toward-Whisper on any check.
"""

from __future__ import annotations

from ..config import AutoSubNet
from ..schema import Segment
from ..subtitles import parse_srt


def pick_auto_key(automatic_captions: dict, target: str | None) -> str | None:
    """Choose the original-audio auto-caption key, or None (-> Whisper).

    Known target L: prefer `L-orig` (yt-dlp's original-audio marker), then plain `L`. Neither -> None.
    Unknown target: use the sole `*-orig` key; 0 or >1 such keys is ambiguous -> None (don't guess)."""
    if target is not None:
        for key in (f"{target}-orig", target):
            if key in automatic_captions:
                return key
        return None
    origs = [k for k in automatic_captions if k.endswith("-orig")]
    return origs[0] if len(origs) == 1 else None


def clean_srt_segments(raw: str) -> list[Segment]:
    """Parse YouTube's server-de-rolled SRT and strip its cosmetics. `parse_srt` handles the timing
    and comma-millisecond format; we only remove leading `>>` speaker-change markers (a documented
    auto-sub artifact). `[music]`/`[applause]` non-speech cues are kept — honest context."""
    out: list[Segment] = []
    for seg in parse_srt(raw):
        text = seg.text
        if text.startswith(">>"):
            text = text[2:].strip()
        if text:
            out.append(Segment(start=seg.start, end=seg.end, text=text))
    return out


def structural_net(
    segments: list[Segment], duration_s: float, net: AutoSubNet
) -> tuple[bool, str]:
    """Language-agnostic pass/fail on an auto-caption candidate. Returns (passed, reason). Any single
    check failing -> reject (caller falls back to Whisper). Coverage and cps are skipped when the
    duration is unknown/zero (presence still applies)."""
    n = len(segments)
    if n < net.min_cues:
        return False, f"only {n} cues (< {net.min_cues})"

    if duration_s and duration_s > 0:
        last_end = max(s.end for s in segments)
        ratio = last_end / duration_s
        if not (net.coverage_min <= ratio <= net.coverage_max):
            return False, (
                f"coverage {ratio:.2f} outside {net.coverage_min}-{net.coverage_max} "
                f"(last cue {last_end:.0f}s vs {duration_s:.0f}s)"
            )
        chars = sum(len(s.text) for s in segments)
        cps = chars / duration_s
        if cps < net.cps_min:
            return False, f"chars-per-second {cps:.2f} < {net.cps_min} (near-empty/music track)"

    return True, "structural net: passed"
