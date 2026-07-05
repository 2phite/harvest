# Genre-Fit Vision Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make harvest's vision stage genre-fit by parameterizing the caption prompt with a caller-supplied `VisionConfig`, adding a hard `max_frames` cap and a per-frame `SKIP` branch, and splitting the stage into a cheap `--frames-only` peek phase plus a `--vision-config` caption phase.

**Architecture:** harvest stays genre-agnostic — the per-genre lens is a caller-authored JSON that fills four slots of a fixed prompt scaffold (unset → a tuned lecture default). Captioning gains a `SKIP` verdict (empty frames stop re-encoding the redundant burned-in caption); frame selection gains a uniform-thinning cap after phash dedup. The stage splits at the cost sink: `--frames-only` extracts+caps frames and stops so a vision-capable caller can peek and write the config before the second run captions from cache.

**Tech Stack:** Python 3.11, pydantic v2, pytest (offline suite — no network/LLM), ffmpeg + imagehash for frames, LM Studio OpenAI-compatible endpoint for captioning.

## Global Constraints

- **Schema stays `schema_version: "1.0"`** — all changes are additive (`Frame.skipped`, `Meta.vision_config`, new `VisionConfig` model, new flags). No breaking field changes; matches the danmaku/interactions additive precedent.
- **Default suite is offline.** Never hit the network, LM Studio, or ffmpeg in a default-run test; inject fakes / monkeypatch (`_ask_image`, `download_video`, `extract_frames`) as the existing tests do.
- **No regression on the lecture sweet spot.** The default (no `--vision-config`) must reproduce lecture behavior or better (the enhanced-lecture default's chrome exclusion is a strict improvement).
- **Caller-gated hard-sub exclusion.** `ocr_scope`'s "exclude the burned-in caption" behavior is only ever what the caller writes — never a harvest default (some videos' on-screen text IS the payload).
- **Contract docs are already written** (`SPEC.md`, `CONTEXT.md`, `PROTOCOL.md` reflect this design). Code must match them; if code and PROTOCOL diverge, PROTOCOL is the target.
- Run tests via the repo venv: `./.venv/Scripts/python.exe -m pytest <path> -v`.

---

### Task 1: Schema — `VisionConfig`, `Frame.skipped`, `Meta.vision_config`

**Files:**
- Modify: `harvest/schema.py` (add `VisionConfig`; add `skipped` to `Frame`; add `vision_config` to `Meta`)
- Test: `tests/test_schema_vision.py` (create)

**Interfaces:**
- Produces: `VisionConfig(focus, look_for, ocr_scope, describe, sample_interval, dedup_threshold, max_frames)` — all fields optional/nullable; `Frame.skipped: bool = False`; `Meta.vision_config: VisionConfig | None = None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_vision.py`:

```python
from harvest.schema import Frame, Meta, VisionConfig


def test_vision_config_all_optional():
    cfg = VisionConfig()
    assert cfg.focus is None and cfg.ocr_scope is None
    assert cfg.sample_interval is None and cfg.max_frames is None


def test_vision_config_roundtrip_json():
    cfg = VisionConfig.model_validate_json(
        '{"focus": "the dish", "ocr_scope": "overlay text only", "max_frames": 40}'
    )
    assert cfg.focus == "the dish"
    assert cfg.ocr_scope == "overlay text only"
    assert cfg.max_frames == 40
    assert cfg.look_for is None


def test_frame_skipped_defaults_false():
    fr = Frame(ts=1.0, phash="abcd")
    assert fr.skipped is False


def test_frame_skipped_true_with_null_caption():
    fr = Frame(ts=1.0, phash="abcd", skipped=True)
    assert fr.skipped is True
    assert fr.caption is None and fr.ocr is None


def test_meta_vision_config_defaults_none():
    m = Meta(cookies_used=False, referer_used=False, tool_version="x")
    assert m.vision_config is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_schema_vision.py -v`
Expected: FAIL — `ImportError: cannot import name 'VisionConfig'`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/schema.py`, add `VisionConfig` immediately after the `Frame` class, add `skipped` to `Frame`, and add `vision_config` to `Meta`:

```python
class Frame(BaseModel):
    ts: float
    path: str | None = None  # relative to the bundle dir; null when --no-frame-images (D8)
    phash: str
    caption: str | None = None
    ocr: str | None = None
    skipped: bool = False  # true = vision model judged the frame empty (caption/ocr both null);
    # kept in bundle.json for provenance, renders nothing in bundle.md. Additive to 1.0.


class VisionConfig(BaseModel):
    """Caller-supplied caption lens (--vision-config). harvest owns no genre taxonomy — the caller
    fills the four prompt slots; any omitted slot uses the tuned lecture default. The three
    frame-selection fields override Settings for this run (omit for defaults)."""

    focus: str | None = None       # what the notes are FOR
    look_for: str | None = None    # where/what to attend
    ocr_scope: str | None = None   # which text to transcribe (caller-gated hard-sub excluder)
    describe: str | None = None    # what the description paragraph covers
    sample_interval: float | None = None
    dedup_threshold: int | None = None
    max_frames: int | None = None
```

Then in `Meta`, add the field:

```python
class Meta(BaseModel):
    cookies_used: bool  # "cookies supplied", NOT "server honored them" (D11)
    referer_used: bool
    vision_model: str | None = None
    vision_config: "VisionConfig | None" = None  # the config used to caption (provenance); null when unset
    tool_version: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_schema_vision.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add harvest/schema.py tests/test_schema_vision.py
git commit -m "feat(schema): add VisionConfig, Frame.skipped, Meta.vision_config (additive 1.0)"
```

---

### Task 2: config — `max_frames` setting

**Files:**
- Modify: `harvest/config.py` (add `max_frames` field + `HARVEST_MAX_FRAMES` env)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: `Settings.max_frames: int` (default 150); `HARVEST_MAX_FRAMES` env override.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_max_frames_default_is_150():
    from harvest.config import Settings
    assert Settings().max_frames == 150


def test_max_frames_env_override(monkeypatch):
    from harvest.config import Settings
    monkeypatch.setenv("HARVEST_MAX_FRAMES", "40")
    assert Settings.load().max_frames == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -k max_frames -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'max_frames'`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/config.py`, add the field to `Settings` next to the other frame knobs (after `phash_dedup_threshold`):

```python
    # hard ceiling on captioned frames per part; uniform-thinned AFTER dedup. The genre-agnostic
    # cost bound — continuous-motion video defeats phash dedup, so a cap is what stops the blowup.
    max_frames: int = 150
```

And in `Settings.load()`, after the `HARVEST_OUT_DIR` block, add:

```python
        if os.environ.get("HARVEST_MAX_FRAMES"):
            s.max_frames = int(os.environ["HARVEST_MAX_FRAMES"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_config.py -k max_frames -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add harvest/config.py tests/test_config.py
git commit -m "feat(config): add max_frames setting (default 150) + HARVEST_MAX_FRAMES"
```

---

### Task 3: frames — uniform-thinning cap

**Files:**
- Modify: `harvest/frames.py` (add `cap_frames`; apply it in `extract_frames` after dedup)
- Test: `tests/test_frames.py` (append)

**Interfaces:**
- Consumes: `Settings.max_frames` (Task 2).
- Produces: `cap_frames(items: list, max_frames: int) -> list` — returns `items` unchanged when `len <= max_frames`, else uniformly thins to exactly `max_frames`, preserving order and always keeping the first element. Applied inside `extract_frames` between `dedup_phashes` and building `Frame`s.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_frames.py`:

```python
from harvest.frames import cap_frames


def test_cap_frames_under_limit_returns_all():
    items = [(float(i), f"{i:016x}") for i in range(5)]
    assert cap_frames(items, 10) == items


def test_cap_frames_over_limit_thins_to_exactly_max():
    items = [(float(i), f"{i:016x}") for i in range(100)]
    capped = cap_frames(items, 10)
    assert len(capped) == 10
    # order preserved, first kept, spread across the range
    assert capped[0] == items[0]
    tss = [ts for ts, _ in capped]
    assert tss == sorted(tss)
    assert tss[-1] >= 80  # spread reaches near the end, not just the first 10


def test_cap_frames_equal_to_limit_returns_all():
    items = [(float(i), f"{i:016x}") for i in range(10)]
    assert cap_frames(items, 10) == items
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_frames.py -k cap_frames -v`
Expected: FAIL — `ImportError: cannot import name 'cap_frames'`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/frames.py`, add `cap_frames` after `dedup_phashes`:

```python
def cap_frames(items: Sequence[tuple[float, str]], max_frames: int) -> list[tuple[float, str]]:
    """Uniform post-dedup thinning to a hard ceiling — the genre-agnostic cost bound (borrowed from
    claude-real-video). Continuous-motion video defeats phash dedup (every sample genuinely differs),
    so without this a long clip captions 100+ frames. Keeps the first frame and spreads the rest
    evenly across the timeline; returns `items` unchanged when already within the cap."""
    items = list(items)
    if len(items) <= max_frames:
        return items
    step = len(items) / max_frames
    keep_idx = {int(i * step) for i in range(max_frames)}
    return [it for i, it in enumerate(items) if i in keep_idx]
```

Then in `extract_frames`, apply it right after the `dedup_phashes` call:

```python
    kept = dedup_phashes([(ts, ph) for ts, ph, _ in candidates], settings.phash_dedup_threshold)
    kept = cap_frames(kept, settings.max_frames)
    kept_ts = {ts for ts, _ in kept}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_frames.py -k cap_frames -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add harvest/frames.py tests/test_frames.py
git commit -m "feat(frames): uniform-thinning max_frames cap after dedup"
```

---

### Task 4: vision — prompt scaffold with slots + lecture default

**Files:**
- Modify: `harvest/vision.py` (replace fixed `CAPTION_PROMPT` with `build_prompt(config)`; bump `PROMPT_VERSION`)
- Test: `tests/test_vision.py` (append)

**Interfaces:**
- Consumes: `VisionConfig` (Task 1).
- Produces: `build_prompt(config: "VisionConfig | None") -> str` — fills a fixed scaffold's four slots from `config`, each unset slot using its lecture default; the scaffold carries the `OCR:`/`DESCRIPTION:` contract and a `SKIP` instruction. `PROMPT_VERSION` bumped to `"2"`. Module-level default constants `DEFAULT_FOCUS/DEFAULT_LOOK_FOR/DEFAULT_OCR_SCOPE/DEFAULT_DESCRIBE`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vision.py`:

```python
from harvest.vision import build_prompt
from harvest.schema import VisionConfig


def test_build_prompt_default_is_lecture_and_has_contract():
    p = build_prompt(None)
    assert "lecture-slide" in p                      # lecture default focus
    assert "OCR:" in p and "DESCRIPTION:" in p        # two-half output contract preserved
    assert "SKIP" in p                                # empty-frame branch present
    assert "exclude" in p.lower()                     # default excludes chrome (burned-in caption)


def test_build_prompt_fills_supplied_slots():
    cfg = VisionConfig(
        focus="the cooking step and dish",
        look_for="the dish, ingredients, and any recipe-card overlay",
        ocr_scope="overlay text only; EXCLUDE the running bottom subtitle",
        describe="the dish, ingredients, and cooking stage",
    )
    p = build_prompt(cfg)
    assert "the cooking step and dish" in p
    assert "recipe-card overlay" in p
    assert "EXCLUDE the running bottom subtitle" in p
    assert "cooking stage" in p
    assert "lecture-slide" not in p                   # lecture default fully overridden


def test_build_prompt_partial_config_falls_back_per_slot():
    cfg = VisionConfig(focus="the game HUD state")
    p = build_prompt(cfg)
    assert "the game HUD state" in p                  # supplied slot used
    assert "reading order" in p                       # ocr_scope fell back to lecture default


def test_prompt_version_bumped():
    from harvest.vision import PROMPT_VERSION
    assert PROMPT_VERSION == "2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vision.py -k "build_prompt or prompt_version" -v`
Expected: FAIL — `ImportError: cannot import name 'build_prompt'`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/vision.py`, bump `PROMPT_VERSION` and replace the `CAPTION_PROMPT` constant with defaults + `build_prompt`:

```python
PROMPT_VERSION = "2"  # bumped: fixed slide prompt -> slot scaffold + SKIP branch (re-captions cache)

# Lecture-slide defaults (the tuned sweet spot). Unset VisionConfig slots fall back to these. The
# ocr_scope/look_for defaults additionally exclude burned-in-caption/watermark/page-counter chrome —
# a strict improvement over the old prompt (96% vs 93% on the lecture control).
DEFAULT_FOCUS = "study notes from a lecture-slide frame"
DEFAULT_LOOK_FOR = (
    "the slide's own headings, body text, figures/diagrams/charts, and any multi-column card grids; "
    "ignore any burned-in caption subtitle, speaker webcam, watermark, and page counter"
)
DEFAULT_OCR_SCOPE = (
    "every piece of visible slide text, verbatim, preserving Chinese, in reading order; "
    "exclude any burned-in caption line, watermark, and page counter"
)
DEFAULT_DESCRIBE = (
    "the slide layout (title slide, body slide, or an N-card grid) and any figures, diagrams, or charts"
)


def build_prompt(config) -> str:
    """Fill the fixed caption scaffold's four slots from a VisionConfig (None/omitted -> lecture
    default per slot). The scaffold keeps the OCR:/DESCRIPTION: output contract intact (so _parse is
    unchanged) and adds a SKIP branch so a genuinely empty frame need not emit forced output."""
    focus = (config.focus if config else None) or DEFAULT_FOCUS
    look_for = (config.look_for if config else None) or DEFAULT_LOOK_FOR
    ocr_scope = (config.ocr_scope if config else None) or DEFAULT_OCR_SCOPE
    describe = (config.describe if config else None) or DEFAULT_DESCRIBE
    return (
        f"You are extracting {focus} from a single video frame.\n"
        f"Attend especially to {look_for}.\n"
        "If the frame carries no caption-worthy content (e.g. a plain talking-head or B-roll whose "
        "only text is a running subtitle), respond with exactly: SKIP\n"
        "Otherwise respond in EXACTLY this format, nothing else:\n"
        f"OCR:\n<{ocr_scope}; or NONE>\n"
        f"DESCRIPTION:\n<one concise paragraph: {describe}; or NONE>"
    )
```

Leave `_parse` and `verify_projector` untouched. (Task 5 rewires `caption_frames` to use `build_prompt` and the SKIP branch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vision.py -v`
Expected: PASS (all — the existing `_parse` tests still pass).

- [ ] **Step 5: Commit**

```bash
git add harvest/vision.py tests/test_vision.py
git commit -m "feat(vision): slot-scaffold build_prompt with lecture default + SKIP instruction"
```

---

### Task 5: vision — SKIP branch + config-driven `caption_frames`

**Files:**
- Modify: `harvest/vision.py` (add `is_skip`; rewire `caption_frames` to accept `config` and set `skipped`)
- Test: `tests/test_vision.py` (append)

**Interfaces:**
- Consumes: `build_prompt` (Task 4), `VisionConfig` (Task 1).
- Produces: `is_skip(text: str) -> bool`; `caption_frames(frames, frame_paths, settings, config=None) -> list[Frame]` — a `SKIP` reply yields `Frame(skipped=True, ocr=None, caption=None)`; otherwise parses as before with `skipped=False`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vision.py`:

```python
def test_is_skip_detects_verdict():
    from harvest.vision import is_skip
    assert is_skip("SKIP") is True
    assert is_skip("  skip \n") is True
    assert is_skip("OCR:\n大家好\nDESCRIPTION:\nA slide.") is False
    assert is_skip("This slide is about skipping steps") is False  # not a leading SKIP


def test_caption_frames_skip_sets_skipped_flag(tmp_path, monkeypatch):
    import harvest.vision as vision
    from harvest.config import Settings
    from harvest.schema import Frame

    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG fake bytes")
    monkeypatch.setattr(vision, "_ask_image", lambda *a, **k: "SKIP")

    frames = [Frame(ts=1.0, path="frames/f.png", phash="abcd")]
    out = vision.caption_frames(frames, {"frames/f.png": png}, Settings())
    assert out[0].skipped is True
    assert out[0].ocr is None and out[0].caption is None


def test_caption_frames_normal_reply_not_skipped(tmp_path, monkeypatch):
    import harvest.vision as vision
    from harvest.config import Settings
    from harvest.schema import Frame

    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG fake bytes")
    monkeypatch.setattr(vision, "_ask_image", lambda *a, **k: "OCR:\n01 / 69\nDESCRIPTION:\nA dark slide.")

    frames = [Frame(ts=1.0, path="frames/f.png", phash="abcd")]
    out = vision.caption_frames(frames, {"frames/f.png": png}, Settings())
    assert out[0].skipped is False
    assert out[0].ocr == "01 / 69"
    assert out[0].caption == "A dark slide."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vision.py -k "is_skip or caption_frames" -v`
Expected: FAIL — `ImportError: cannot import name 'is_skip'` (and `caption_frames` TypeError on the fake).

- [ ] **Step 3: Write minimal implementation**

In `harvest/vision.py`, add `is_skip` (near `_parse`) and rewrite `caption_frames`:

```python
def is_skip(text: str) -> bool:
    """True when the model returned the SKIP verdict (empty frame). Leading-token match so a caption
    that merely mentions 'skip' in prose is not a false positive."""
    return text.strip().upper().startswith("SKIP")
```

```python
def caption_frames(
    frames: list[Frame], frame_paths: dict[str, Path], settings: Settings, config=None
) -> list[Frame]:
    """Caption each frame independently (SPEC §5 step 4). Call verify_projector first (D7). The prompt
    is built from `config` (None -> lecture default). A SKIP reply marks the frame skipped (both
    halves null); otherwise the OCR:/DESCRIPTION: reply is parsed as before."""
    client = _client(settings)
    model = settings.lmstudio_vision_model
    prompt = build_prompt(config)
    out: list[Frame] = []
    for fr in frames:
        src = frame_paths.get(fr.path or "")
        png = Path(src).read_bytes() if src else b""
        text = _ask_image(client, model, png, prompt) if png else ""
        if text and is_skip(text):
            out.append(fr.model_copy(update={"ocr": None, "caption": None, "skipped": True}))
            continue
        ocr, caption = _parse(text)
        out.append(fr.model_copy(update={"ocr": ocr, "caption": caption, "skipped": False}))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_vision.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add harvest/vision.py tests/test_vision.py
git commit -m "feat(vision): SKIP branch + config-driven caption_frames"
```

---

### Task 6: merge — skipped frames neither bound nor render chunks

**Files:**
- Modify: `harvest/merge.py` (filter `skipped` frames out of chunking + rendering in `render_markdown`)
- Test: `tests/test_merge.py` (append)

**Interfaces:**
- Consumes: `Frame.skipped` (Task 1).
- Produces: `render_markdown` uses only non-skipped frames for chunk boundaries and slide lines; skipped frames stay in `bundle.frames` (JSON) but produce no `### [mm:ss]` header and no slide text.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_merge.py` (follow the file's existing Bundle-construction helper style; if none, build inline as below):

```python
def test_render_markdown_omits_skipped_frames():
    from harvest.config import Settings
    from harvest.merge import render_markdown
    from harvest.schema import (
        Bundle, Frame, Meta, Segment, Transcript,
    )

    bundle = Bundle(
        platform="bilibili.com", id="BVx", part=1, url="u",
        duration_s=60, fetched_at="2026-07-05T00:00:00Z",
        transcript=Transcript(
            source="whisper", source_reason="test",
            segments=[Segment(start=0.0, end=5.0, text="hello world")],
        ),
        frames=[
            Frame(ts=0.0, phash="a", caption="A real slide.", skipped=False),
            Frame(ts=30.0, phash="b", caption=None, ocr=None, skipped=True),
        ],
        meta=Meta(cookies_used=False, referer_used=False, tool_version="x"),
    )
    md = render_markdown(bundle, Settings())
    assert "A real slide." in md          # non-skipped frame renders
    assert "[00:30]" not in md            # skipped frame does not open a chunk
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_merge.py -k skipped -v`
Expected: FAIL — a `### [00:30]` header appears because the skipped frame currently seeds a chunk boundary.

- [ ] **Step 3: Write minimal implementation**

In `harvest/merge.py`, inside `render_markdown`, replace the `bundle.frames` usages (the empty-guard and the `chunk(...)` call) with a skipped-filtered list. Find:

```python
    if not t.segments and not bundle.frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        return "\n".join(lines) + "\n"

    for ch in chunk(
        t.segments, bundle.frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
    ):
```

Replace with:

```python
    # Skipped frames (vision SKIP verdict) carry no caption: they neither seed a chunk boundary nor
    # render a slide line. They stay in bundle.json (bundle.frames) for provenance.
    visible_frames = [f for f in bundle.frames if not f.skipped]

    if not t.segments and not visible_frames:
        lines.append("_(no transcript yet — Whisper pending)_")
        return "\n".join(lines) + "\n"

    for ch in chunk(
        t.segments, visible_frames, window_s=settings.chunk_window_s, duration_s=bundle.duration_s
    ):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_merge.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "feat(merge): skipped frames excluded from chunking + rendering"
```

---

### Task 7: merge — `write_frames_only` peek writer

**Files:**
- Modify: `harvest/merge.py` (add `write_frames_only`)
- Test: `tests/test_merge.py` (append)

**Interfaces:**
- Consumes: `Frame` (Task 1), `Canonical`.
- Produces: `write_frames_only(canonical, frames: list[Frame], frame_sources: dict[str, Path], settings) -> Path` — writes PNGs to `out/<id>-p<part>/frames/` and a `frames.json` index (`[{ts, path, phash}]`); returns the out dir. No transcript, no captions, no `bundle.md`/`bundle.json`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_merge.py`:

```python
def test_write_frames_only_writes_pngs_and_index(tmp_path):
    import json
    from harvest.config import Settings
    from harvest.merge import write_frames_only
    from harvest.providers.base import Canonical
    from harvest.schema import Frame

    src = tmp_path / "raw_f.png"
    src.write_bytes(b"\x89PNG")
    settings = Settings()
    settings.out_dir = tmp_path / "out"
    canonical = Canonical(platform="bilibili.com", id="BVx", part=1, url="u")
    frames = [Frame(ts=6.0, path="frames/000006_000.png", phash="deadbeef")]

    out = write_frames_only(canonical, frames, {"frames/000006_000.png": src}, settings)

    assert (out / "frames" / "000006_000.png").exists()
    index = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    assert index == [{"ts": 6.0, "path": "frames/000006_000.png", "phash": "deadbeef"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_merge.py -k frames_only -v`
Expected: FAIL — `ImportError: cannot import name 'write_frames_only'`.

- [ ] **Step 3: Write minimal implementation**

In `harvest/merge.py`, add (top of file already imports `json`? it imports `yaml`, `shutil`, `re`; add `import json` at the top with the other stdlib imports):

```python
def write_frames_only(
    canonical: Canonical, frames: list[Frame], frame_sources: dict[str, Path], settings: Settings
) -> Path:
    """Peek phase (--frames-only): write extracted frame PNGs + a frames.json index to
    out/<id>-p<part>/, and STOP. No transcript, no captions. Lets a vision-capable caller inspect the
    frames and author a --vision-config before the (expensive) captioning phase."""
    out = settings.out_dir / f"{canonical.id}-p{canonical.part}"
    frames_dir = out / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for fr in frames:
        if fr.path and fr.path in frame_sources:
            shutil.copy2(frame_sources[fr.path], out / fr.path)
    index = [{"ts": fr.ts, "path": fr.path, "phash": fr.phash} for fr in frames]
    (out / "frames.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out
```

Add `import json` near the top of `harvest/merge.py` if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_merge.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add harvest/merge.py tests/test_merge.py
git commit -m "feat(merge): write_frames_only peek writer (frames + frames.json)"
```

---

### Task 8: cli — `--max-frames`, `--vision-config`, `--frames-only` wiring

**Files:**
- Modify: `harvest/cli.py` (new flags; `apply_overrides`; `_caption` cache-key + config; `process_part` peek branch + config threading + `meta.vision_config`)
- Modify: `harvest/merge.py::build_bundle` (accept + set `vision_config`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `VisionConfig` (Task 1), `write_frames_only` (Task 7), `caption_frames`/`build_prompt`/`PROMPT_VERSION` (Tasks 4–5), `Settings.max_frames` (Task 2).
- Produces: `parse_args` accepts `--max-frames N`, `--vision-config FILE`, `--frames-only`; `apply_overrides` applies `--max-frames`; `_load_vision_config(args) -> VisionConfig | None`; `_caption(canonical, frames, frame_sources, settings, config)` folds the config hash into the caption cache key; `build_bundle(..., vision_config=None)` sets `Meta.vision_config`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_parse_args_vision_flags():
    from harvest.cli import parse_args
    args = parse_args([
        "ingest", "https://www.bilibili.com/video/BVx",
        "--frames-only", "--max-frames", "40", "--vision-config", "cfg.json",
    ])
    assert args.frames_only is True
    assert args.max_frames == 40
    assert args.vision_config == "cfg.json"


def test_apply_overrides_max_frames():
    from types import SimpleNamespace
    from harvest.cli import apply_overrides
    from harvest.config import Settings
    settings = Settings()
    args = SimpleNamespace(
        out=None, dedup_threshold=None, scene_threshold=None, max_frames=40,
    )
    apply_overrides(settings, args)
    assert settings.max_frames == 40


def test_load_vision_config_reads_json(tmp_path):
    from harvest.cli import _load_vision_config
    from types import SimpleNamespace
    cfg = tmp_path / "vision.json"
    cfg.write_text('{"focus": "the dish", "max_frames": 30}', encoding="utf-8")
    out = _load_vision_config(SimpleNamespace(vision_config=str(cfg)))
    assert out.focus == "the dish"
    assert out.max_frames == 30
    assert _load_vision_config(SimpleNamespace(vision_config=None)) is None


def test_caption_cache_key_differs_by_config(tmp_path):
    from harvest.cli import _caption_cache_key
    from harvest.config import Settings
    from harvest.providers.base import Canonical
    from harvest.schema import Frame, VisionConfig
    settings = Settings()
    canonical = Canonical(platform="bilibili.com", id="BVx", part=1, url="u")
    frames = [Frame(ts=0.0, phash="aa"), Frame(ts=6.0, phash="bb")]
    k_default = _caption_cache_key(canonical, frames, settings, None)
    k_cooking = _caption_cache_key(canonical, frames, settings, VisionConfig(focus="the dish"))
    assert k_default != k_cooking
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_cli.py -k "vision or max_frames or caption_cache" -v`
Expected: FAIL — new flags/functions absent.

- [ ] **Step 3: Write minimal implementation**

In `harvest/cli.py`:

(a) Add the flags in `parse_args`, next to the existing frame flags:

```python
    ingest.add_argument("--frames-only", action="store_true",
        help="peek: extract + cap frames, write them + frames.json, STOP before captioning")
    ingest.add_argument("--vision-config", default=None,
        help="path to a JSON VisionConfig shaping the caption prompt + frame selection")
    ingest.add_argument("--max-frames", type=int, default=None,
        help="hard ceiling on captioned frames per part (default 150)")
```

(b) In `apply_overrides`, add before the `return`:

```python
    if getattr(args, "max_frames", None) is not None:
        settings.max_frames = args.max_frames
```

(c) Add config loading + cache-key helpers (top-level in cli.py):

```python
def _load_vision_config(args):
    from pathlib import Path

    from .schema import VisionConfig

    path = getattr(args, "vision_config", None)
    if not path:
        return None
    return VisionConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _caption_cache_key(canonical, frames, settings, config):
    from .vision import PROMPT_VERSION

    frameset = hashlib.sha1("".join(f.phash for f in frames).encode()).hexdigest()[:10]
    config_hash = (
        hashlib.sha1(config.model_dump_json().encode()).hexdigest()[:10] if config else "default"
    )
    return fs_key(
        canonical.platform, canonical.id, canonical.part,
        stage="captions", model=settings.lmstudio_vision_model,
        prompt=PROMPT_VERSION, frameset=frameset, config=config_hash,
    )
```

(d) Rewrite `_caption` to take `config` and use the helper:

```python
def _caption(canonical, frames, frame_sources, settings, config):
    """Step 5: D7 projector probe + per-frame captioning, all-or-nothing caption cache (D10).
    Cache key includes the VisionConfig hash, so re-configuring re-captions only."""
    from .vision import caption_frames, verify_projector

    key = _caption_cache_key(canonical, frames, settings, config)
    cached = load_json(settings.cache_dir, "captions", key)
    if cached is not None:
        print(f"[{canonical.id} p{canonical.part}] captions: cached ({len(cached)})")
        return [Frame(**f) for f in cached]

    verify_projector(settings)  # D7: hard-stop if the mmproj isn't really reading images
    print(f"[{canonical.id} p{canonical.part}] captioning {len(frames)} frames via "
          f"{settings.lmstudio_vision_model}...")
    captioned = caption_frames(frames, frame_sources, settings, config)
    save_json(settings.cache_dir, "captions", key, [f.model_dump() for f in captioned])
    return captioned
```

(Remove the now-unused `PROMPT_VERSION` import at the top of the old `_caption` if present; `_caption_cache_key` imports it locally.)

(e) In `process_part`, add the peek branch + config threading. Replace the vision block:

```python
    config = _load_vision_config(args)
    if config:
        if config.sample_interval is not None:
            settings.sample_interval_s = config.sample_interval
        if config.dedup_threshold is not None:
            settings.phash_dedup_threshold = config.dedup_threshold
        if config.max_frames is not None:
            settings.max_frames = config.max_frames

    provider = select_provider(canonical.url)
    meta = provider.fetch_metadata(canonical, settings)

    if args.frames_only:
        from .frames import download_video, extract_frames
        from .merge import write_frames_only

        print(f"[{canonical.id} p{canonical.part}] peek: preparing video + frames...")
        video = download_video(canonical, settings)
        frames, frame_sources = extract_frames(canonical, video, settings)
        out = write_frames_only(canonical, frames, frame_sources, settings)
        print(f"[{canonical.id} p{canonical.part}] {len(frames)} frames -> {out} "
              f"(peek; no captions). Inspect frames/, author a --vision-config, then re-run.")
        return

    transcript = decide_transcript(canonical, meta, settings, args)

    frames = []
    frame_sources = {}
    vision_model = None
    if not args.no_vision:
        from .frames import download_video, extract_frames

        print(f"[{canonical.id} p{canonical.part}] preparing video + frames...")
        video = download_video(canonical, settings)
        frames, frame_sources = extract_frames(canonical, video, settings)
        print(f"[{canonical.id} p{canonical.part}] {len(frames)} frames after dedup + cap")
        if frames:
            frames = _caption(canonical, frames, frame_sources, settings, config)
            vision_model = settings.lmstudio_vision_model
```

Note the top of `process_part` currently begins with `provider = select_provider(...)` and `meta = provider.fetch_metadata(...)`; the block above moves those below the config load. Keep the rest of `process_part` (danmaku, interactions, `build_bundle`, `write_bundle`) unchanged except passing `vision_config=config` to `build_bundle` (next step).

(f) In the `build_bundle(...)` call inside `process_part`, add `vision_config=config`:

```python
    bundle = build_bundle(
        canonical, meta, transcript, frames, settings,
        vision_model=vision_model, vision_config=config,
        danmaku=danmaku, interactions=interactions,
    )
```

(g) In `harvest/merge.py::build_bundle`, add the parameter and thread it into `Meta`:

```python
def build_bundle(
    canonical: Canonical,
    meta: SourceMetadata,
    transcript: Transcript,
    frames: list[Frame],
    settings: Settings,
    *,
    vision_model: str | None = None,
    vision_config=None,
    danmaku: Danmaku | None = None,
    interactions: Interactions | None = None,
) -> Bundle:
```

and in the `Meta(...)` construction inside it:

```python
        meta=Meta(
            cookies_used=bool(settings.sessdata or settings.cookies_browser),
            referer_used=(canonical.platform == "bilibili.com"),
            vision_model=vision_model, vision_config=vision_config,
            tool_version=settings.tool_version,
        ),
```

- [ ] **Step 4: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all — new tests + existing suite green; the pre-existing `_caption` test in `test_cli.py`, if any, may need the extra `config` arg — update its call to `_caption(..., None)` and re-run).

- [ ] **Step 5: Commit**

```bash
git add harvest/cli.py harvest/merge.py tests/test_cli.py
git commit -m "feat(cli): --frames-only peek, --vision-config, --max-frames; config in caption cache + meta"
```

---

### Task 9: End-to-end verification (real videos, manual gate)

**Files:**
- Test: none (manual/observational — this is the verify step, not an offline unit test)

**Interfaces:**
- Consumes: the full pipeline (Tasks 1–8), LM Studio VL model live, Firefox bilibili cookies.

- [ ] **Step 1: Peek phase on an off-genre video**

Run:
```bash
./.venv/Scripts/python.exe -m harvest.cli ingest "https://www.bilibili.com/video/BV1V4Te6MEAu" --frames-only --max-frames 40
```
Expected: `out/BV1V4Te6MEAu-p1/frames/` populated (≤ 40 PNGs — the cap fired vs the 186 uncapped) and `frames.json` present; no `bundle.md`/`bundle.json`; message ends "peek; no captions".

- [ ] **Step 2: Author a VisionConfig from the peeked frames**

Read a few `out/BV1V4Te6MEAu-p1/frames/*.png`, then write `scratch/vision_finance.json`:
```json
{
  "focus": "the informational claim or data shown on-screen (entities, figures, relationships)",
  "look_for": "embedded graphic/article text, big number+unit callouts, and diagram arrows linking companies; ignore the bottom hard-sub and channel watermark",
  "ocr_scope": "all text baked into the graphic/screenshot verbatim, Chinese and English with figures and units; EXCLUDE the bottom-center subtitle line and the corner watermark",
  "describe": "classify the frame (talking-head / number card / article / relationship diagram / stage photo / B-roll) and state the data or relationship it conveys",
  "max_frames": 40
}
```

- [ ] **Step 3: Caption phase reuses cached frames**

Run:
```bash
./.venv/Scripts/python.exe -m harvest.cli ingest "https://www.bilibili.com/video/BV1V4Te6MEAu" --vision-config scratch/vision_finance.json
```
Expected: no re-download / no re-extract (frames cached); talking-head/B-roll frames come back `skipped: true` in `bundle.json` and absent from `bundle.md`; graphic frames carry genre-fit captions; `meta.vision_config` echoes the config.

- [ ] **Step 4: Lecture no-regression check**

Run (no config → enhanced-lecture default):
```bash
./.venv/Scripts/python.exe -m harvest.cli ingest "<a lecture-slide bilibili URL>"
```
Expected: slide OCR/figure captions comparable to before, with burned-in caption/watermark now excluded from OCR. Confirm `bundle.md` chunks read cleanly.

- [ ] **Step 5: Full offline suite green**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all).

---

## Self-Review

**Spec coverage** (against the consolidated SPEC §5/§8, CONTEXT Vision terms, PROTOCOL Frames/VisionConfig):
- VisionConfig scaffold + 4 slots + lecture default → Tasks 1, 4.
- Caller-gated `ocr_scope` hard-sub exclusion → Task 4 (default excludes chrome; supplied slot overrides).
- SKIP branch (schema + verdict + render) → Tasks 1, 5, 6.
- `max_frames` cap (uniform thinning) → Tasks 2, 3.
- Two-phase peek (`--frames-only`) → Tasks 7, 8.
- `--vision-config` + config hash in caption cache key + `meta.vision_config` provenance → Task 8.
- `sample_interval`/`dedup_threshold`/`max_frames` config overrides → Task 8 (process_part threading).
- Docs (SPEC/CONTEXT/PROTOCOL) — already consolidated in Stage 3; Task 9 verifies code matches.
- Deferred (NOT in this plan, by design): structured table/UI-state field; scene-change sampling.

**Placeholder scan:** none — every code step carries complete code.

**Type consistency:** `build_prompt(config)` (Task 4) ← `caption_frames(..., config)` (Task 5) ← `_caption(..., config)` and `_caption_cache_key(..., config)` (Task 8); `write_frames_only(canonical, frames, frame_sources, settings)` (Task 7) ← called in `process_part` peek branch (Task 8); `Frame.skipped` (Task 1) ← set in Task 5, read in Task 6; `Meta.vision_config` (Task 1) ← set via `build_bundle(vision_config=...)` (Task 8). Consistent.
