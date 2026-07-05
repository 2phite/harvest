"""YouTube auto-caption acquisition + structural validity net (SPEC §6).

Pure helpers, provider-orchestrated. The net is LANGUAGE-AGNOSTIC and structural (presence,
coverage, chars-per-second) — deliberately NOT the CJK `harvest/quality.py` gate, which can't be
calibrated across YouTube's ~150 language variants. Fail-toward-Whisper on any check.
"""

from __future__ import annotations

from ..config import AutoSubNet
from ..schema import Segment
from ..subtitles import parse_srt


def _lang_base(tag: str) -> str:
    """Primary language subtag of a BCP-47 tag: `en-US`->`en`, `zh-Hant`/`zh-Hans-CN`->`zh`."""
    return tag.split("-", 1)[0]


def pick_auto_key(automatic_captions: dict, target: str | None) -> str | None:
    """Choose the original-audio auto-caption key, or None (-> Whisper).

    Known target L: prefer the exact `L-orig` (yt-dlp's original-audio marker), then exact `L`.
    Failing an exact hit, tolerate REGIONAL and SCRIPT variants — yt-dlp's `info["language"]` is
    best-effort and often a fuller tag (`en-US`, `zh-Hant`, `pt-BR`) than the caption keys, which
    may be bare (`en`) or script-tagged (`zh-Hans-orig`). Match on the primary language SUBTAG,
    staying original-audio-safe:
      1. any `*-orig` key in the same base language (script/region ignored — `-orig` guarantees
         it is the original audio, so `zh-Hant` reuses `zh-Hans-orig`; a key also matching the
         fuller target tag is preferred);
      2. else, ONLY when the video has no `*-orig` keys at all (single-audio, so a same-language
         key is the original ASR, not a machine translation), the same-base plain key (bare or
         shortest first).
    No same-language match -> None. Unknown target: the sole `*-orig` key; 0 or >1 -> None (don't
    guess)."""
    if target is None:
        origs = [k for k in automatic_captions if k.endswith("-orig")]
        return origs[0] if len(origs) == 1 else None

    for key in (f"{target}-orig", target):
        if key in automatic_captions:
            return key

    base = _lang_base(target)

    # a key also matching the fuller target tag wins, then the shorter (more generic) key.
    def _rank(k: str) -> tuple[int, int]:
        return (0 if k.startswith(target) else 1, len(k))

    same_base_orig = sorted(
        (k for k in automatic_captions if k.endswith("-orig") and _lang_base(k[:-5]) == base),
        key=_rank,
    )
    if same_base_orig:
        return same_base_orig[0]

    if not any(k.endswith("-orig") for k in automatic_captions):
        same_base = sorted((k for k in automatic_captions if _lang_base(k) == base), key=_rank)
        if same_base:
            return same_base[0]
    return None


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
