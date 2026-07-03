# harvest — downstream (Atlas) protocol

The machine-facing contract between the **Atlas** project and **harvest**. Atlas codes against the
shapes here; treat them as a stable API. Design rationale lives in [SPEC.md](SPEC.md) — you should not
need it to update an Atlas skill against this contract.

harvest supersedes `bili-tool`. The contract is **multi-source**: `platform` distinguishes the source.
This is a fresh `1.0` contract, not a bili-tool patch — fields that were bilibili-specific are
generalized (see §Changes-from-bili-tool at the end).

## CLI verbs

```bash
harvest ingest <url> [flags]   # full pipeline -> out/<id>-p<part>/ bundle
harvest probe  <url>           # cheap pre-flight metadata only, no media
```

There is no bare-url form. Supported sources: `bilibili.com`, `youtube.com`.

### `ingest` flags

```
--part N        1-based part index (default: from URL; YouTube is always 1)
--all-parts     loop every part, isolate-and-continue (bilibili multi-part)
--force-whisper skip subtitle reuse, always transcribe
--lang CODE     pin transcription language (default: zh for bilibili, auto-detect for YouTube)
--robust        disable condition_on_previous_text (repetition-loop lectures)
--no-vision     skip frame captioning
--dedup-threshold N   phash hamming distance to collapse near-duplicate frames (default 10)
--out DIR       output root (default ./out)
--no-frame-images     omit PNGs from out/ (JSON still records phash/ts/caption)
--danmaku       opt-in: fetch + mirror the bilibili audience danmaku track (bilibili.com only;
                default OFF; a graceful no-op with a warning on YouTube)
```

## `probe` — pre-flight metadata

- Takes **only** a URL — no other flags apply.
- **stdout carries the JSON result and nothing else** (one line, safe to pipe into a parser).
- Diagnostics/errors go to **stderr**. Exit **0** on success, **1** on failure (stderr:
  `error: <message>`, stdout empty).

### `ProbeResult` shape (matches `schema.py::ProbeResult`)

| field | type | nullable? | notes |
|---|---|---|---|
| `schema_version` | string | no | currently `"1.0"` |
| `platform` | string | no | `"bilibili.com"` or `"youtube.com"` |
| `id` | string | no | canonical video id (bilibili `BV…`; YouTube 11-char id) |
| `title` | string | yes | |
| `uploader` | string | yes | uploader/channel display name |
| `uploader_id` | string | yes | stable author id — bilibili member id (as string) or YouTube channel id (`UC…`) |
| `description` | string | yes | video description |
| `duration_s` | integer | yes | total duration in seconds |
| `published_at` | string | yes | ISO 8601 with explicit offset; **per-source tz** (bilibili `+08:00`, YouTube `Z`/UTC) |
| `thumbnail_url` | string | yes | video thumbnail image URL |
| `fetched_at` | string | yes | ISO 8601 UTC (`Z`) — when this probe ran |
| `stats` | object | yes | engagement snapshot @ `fetched_at`; see `Stats` shape below |
| `parts` | integer | no | number of parts (always ≥ 1; YouTube always 1) |
| `part_durations_s` | array of (integer or null) | — | one entry per part, index-aligned to part 1..N; entries may be `null` |

### `Stats` shape (matches `schema.py::Stats`)

Engagement metrics, all nullable, all optional integers:

| field | notes |
|---|---|
| `view_count` | bilibili + YouTube |
| `like_count` | bilibili + YouTube |
| `coin_count` | bilibili only (硬币); `null` on YouTube |
| `favorite_count` | bilibili only (收藏); `null` on YouTube |
| `share_count` | bilibili only (分享); `null` on YouTube |
| `reply_count` | top-level comment count; bilibili only; `null` on YouTube |
| `danmaku_count` | bilibili total danmaku count; `null` on YouTube — **the (future) `--danmaku` opt-in signal** |

### Example (YouTube)

```json
{
  "schema_version": "1.0",
  "platform": "youtube.com",
  "id": "dQw4w9WgXcQ",
  "title": "Example Talk",
  "uploader": "Example Channel",
  "uploader_id": "UCabc123...",
  "description": "…",
  "duration_s": 2760,
  "published_at": "2024-06-28T16:00:00Z",
  "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
  "fetched_at": "2026-07-01T12:00:00Z",
  "stats": {
    "view_count": 1700000000, "like_count": 18000000,
    "coin_count": null, "favorite_count": null, "share_count": null,
    "reply_count": null, "danmaku_count": null
  },
  "parts": 1,
  "part_durations_s": [2760]
}
```

### Nulls are normal, not exceptional

`probe` reports best-effort metadata from a single upstream call. Any of `title`, `uploader`,
`uploader_id`, `description`, `duration_s`, `published_at`, `thumbnail_url`, or any field inside
`stats` may be `null` on an otherwise-successful call. `part_durations_s` is always present and
length-aligned to `parts`, but individual entries may be `null`. **Atlas must tolerate all of these
as `null`/missing, not as failures.**

`bilibili.tv` is unsupported by `probe` (deferred): a `.tv` URL exits 1 with
`error: probe is bilibili.com-only; bilibili.tv unsupported (deferred)`. Treat a nonzero exit as "no
probe data for this URL."

## `ingest` — bundle output

Output is `out/<id>-p<part>/` containing `bundle.md`, `bundle.json`, and `frames/`.

- **`bundle.md` is the primary ingestion surface** — Atlas reads this prose. It opens with a
  frontmatter header carrying provenance (platform, id, url, title, uploader, `published_at`,
  `transcript_source` + decision reason, vision model, tool version), then slide-chunked
  transcript + visual notes.
- **`bundle.json` is the precise backing record** — same facts, structured. Mirrors `ProbeResult`'s
  metadata fields plus:

```jsonc
{
  "schema_version": "1.0",
  "platform": "youtube.com",           // or "bilibili.com"
  "id": "…", "part": 1, "url": "…",
  "title": "…", "uploader": "…", "uploader_id": "…", "description": "…",
  "duration_s": 2760, "published_at": "…",
  "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
  "fetched_at": "2026-07-01T12:00:00Z",
  "stats": {
    "view_count": 1700000000, "like_count": 18000000,
    "coin_count": null, "favorite_count": null, "share_count": null,
    "reply_count": null, "danmaku_count": null
  },
  "transcript": {
    "source": "human-sub",             // "human-sub" | "auto-sub" | "whisper"  ← provenance
    "language": "en",                  // language axis, separate from source
    "model": "large-v3",               // whisper model; null when source is a caption
    "robust": false,
    "quality_gate": { … } | null,      // populated only when a caption was gated (bilibili)
    "segments": [ { "start": 0.0, "end": 4.2, "text": "…" } ]
  },
  "frames": [ { "ts": 12.5, "path": "frames/000012_500.png", "phash": "…", "caption": "…", "ocr": "…" } ],
  "danmaku": null,                     // populated only when `--danmaku` ran on a supporting platform
  "meta": { "cookies_used": true, "referer_used": true, "vision_model": "…", "tool_version": "…" }
}
```

### `Danmaku` shapes (matches `schema.py::Danmaku`/`DanmakuWindow`/`DanmakuLine`) — `--danmaku` opt-in

`bundle.danmaku` is `null` unless `harvest ingest --danmaku` was passed **and** the platform supports
it (bilibili.com only; YouTube has no danmaku concept, so `--danmaku` on a YouTube URL prints a
warning and leaves `bundle.danmaku` `null` — same as not passing the flag). When `--danmaku` runs on
bilibili and finds nothing, `bundle.danmaku` is still populated (not null) with `fetched_total: 0` and
`windows: []` — "requested, found nothing" is distinct from "not requested."

```jsonc
"danmaku": {
  "source_total": 12000,             // bilibili's platform-reported total; null if unavailable
  "fetched_total": 11400,            // census pull via the protobuf endpoint: ~90-94% of source_total;
                                      // the gap is deleted/shielded danmaku, not sampling
  "model": "qwen2.5-7b-instruct",    // the LLM that produced the mirror below; provenance
  "windows": [
    {
      "start": 0.0, "end": 15.0,     // content-time window on danmaku's OWN fixed cadence (~15s)
      "total": 340,                  // raw danmaku count in this window, BEFORE clustering
      "lines": [
        { "text": "草", "count": 12, "high_like": false },
        { "text": "这个技巧太强了", "count": 1, "high_like": true }
      ]
    }
  ]
}
```

`DanmakuWindow`s are fixed ~15s content-time buckets on danmaku's **own** cadence — deliberately
finer than, and independent of, the transcript `## [mm:ss]` chunk marks (the crowd's pace tracks
seconds, not slide cuts). To cross-reference a crowd reaction against the transcript/frames, compare
the window's `[start, end)` seconds to the chunk timestamps; do not expect a window to coincide with
a single chunk. Timing is deliberately vague (viewers react a few seconds late) — treat a window as
"around this stretch," never as frame-accurate.

**`Danmaku` is a fenced MIRROR, not interpreted content**: every `DanmakuLine.text` is verbatim —
never paraphrased, translated, decoded, or labeled with sentiment/topic. Lines within a window are
chronological by content time, never sorted by count.

**`DanmakuLine.high_like`** marks bilibili's own 高赞 (platform-promoted) flag, extracted verbatim
from the protobuf record *before* clustering (never absorbed into a flood's `count`) and never
LLM-decided. Unlike the rest of the danmaku track, `high_like` is **higher authority than the
surrounding mirror** — it's a platform fact (bilibili's own promotion decision), not crowd opinion to
be doubted. See the authority carve-out below.

**Danmaku authority: strictly BELOW `transcript`.** It is crowd expression — jokes, memes, sarcasm,
frequently factually wrong — never treat it as authoritative content; it is signal about audience
reaction, not a source of facts about the video. **Carve-out:** `high_like` is the one danmaku field
that is *not* crowd-opinion-to-be-doubted — it's bilibili's own platform signal for which lines it
promoted, so treat `high_like: true` as trustworthy provenance about audience reaction (still not a
source of video facts, just a fact about which reaction the platform surfaced).

**bundle.json is always complete; bundle.md is capped.** `bundle.json`'s `danmaku.windows[].lines` is
the full, uncapped set, in chronological order by content time. `bundle.md` renders a dedicated
`## Danmaku` section (below the transcript chunks) with a one-line provenance note plus, per
non-empty window, a `### [mm:ss] (N danmaku)` header and a **two-cap** rendering: promoted
(`high_like: true`) lines render FIRST as `- 👍 「text」 ×count`, capped at 20, followed by ordinary
lines as `- 「text」 ×count`, capped at 50 (the `×count` suffix omitted when `count == 1`) — each
group gets its OWN `- ﹢N more — see bundle.json` overflow marker beyond its cap. Because bundle.md
groups promoted lines ahead of ordinary ones, the "chronological by content time" guarantee holds
*within* each rendered group and across the complete `bundle.json`, but not across the md's
promoted/ordinary split — bundle.json preserves the full chronological interleave regardless of
`high_like`. Atlas needing the complete set, or the true chronological order across promoted and
ordinary lines, must read `bundle.json`.

### Stable vs volatile fields

`ProbeResult` and `Bundle` mix two kinds of fields:

- **Stable (intrinsic) fields** — `platform, id, title, uploader, uploader_id, description,
  duration_s, published_at, thumbnail_url, parts` — describe the video itself and don't meaningfully
  change between fetches (a thumbnail may be swapped by the uploader, but it's still descriptive
  metadata, not an engagement count).
- **`stats` is a volatile snapshot** — a point-in-time engagement count as of the enclosing record's
  `fetched_at`. Counts generally grow but can be reset or hidden by the uploader/platform. **Never
  compare `stats` across two bundles/probes without accounting for each record's own `fetched_at`.**

`stats.danmaku_count` is (in a later feature) the signal hermes reads to decide the `--danmaku`
opt-in: bilibili videos with a nonzero danmaku count are candidates for danmaku ingestion; YouTube's
`danmaku_count` is always `null` (no danmaku concept on that platform).

### Provenance authority (how Atlas should rank competing transcripts)

`transcript.source` is a **source-authority signal**, not cosmetic. Rank:
**`human-sub` > `whisper` > `auto-sub`.** Rationale: a human/original caption is authoritative; a
clean local Whisper transcript is trustworthy; a machine auto-caption is a coin-flip. `language` is a
separate axis — a `whisper` transcript's language is Whisper's detected (or `--lang`-pinned) language.

> harvest currently produces `auto-sub` only on the bilibili path (when its quality gate passes).
> YouTube produces only `human-sub` or `whisper` (auto-captions are skipped by design).

## Changes from the bili-tool contract (for migrating an existing Atlas skill)

- `platform` gains `"youtube.com"`.
- `uploader_mid` (integer, bilibili-only) is **removed**; use **`uploader_id`** (string, all sources).
- `transcript.source` value `"ai-zh"` is **renamed** to `"auto-sub"`; language moves to
  `transcript.language`.
- `published_at` offset is now **per-source** (was always `+08:00`); read the offset from the value.
- Invocation is `harvest …`, not `bili-tool …`.
