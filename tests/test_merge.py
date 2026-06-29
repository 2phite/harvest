import json

from bili_tool.config import Settings
from bili_tool.merge import chunk, write_bundle
from bili_tool.schema import Bundle, Frame, Meta, Segment, Transcript


def _seg(start, end, text="x"):
    return Segment(start=start, end=end, text=text)


def _bundle_with_frame(rel_path):
    return Bundle(
        platform="bilibili.com",
        id="BV1",
        part=1,
        url="https://b/video/BV1",
        fetched_at="2026-06-29T00:00:00Z",
        transcript=Transcript(source="whisper", source_reason="test", segments=[_seg(0, 5)]),
        frames=[Frame(ts=2.0, path=rel_path, phash="abc", ocr="SLIDE TEXT", caption="a chart")],
        meta=Meta(cookies_used=False, referer_used=True, tool_version="t"),
    )


def test_no_frame_images_omits_png_nulls_path_keeps_caption(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(
        bundle, settings,
        frame_sources={"frames/00002000.png": src},
        frame_images=False,
    )

    assert not (out / "frames").exists()  # D8: no PNGs shipped
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] is None  # path nulled
    assert data["frames"][0]["phash"] == "abc"  # but record retained
    md = (out / "bundle.md").read_text(encoding="utf-8")
    assert "SLIDE TEXT" in md and "a chart" in md  # caption text still in the product


def test_default_ships_png_and_keeps_path(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG fake")
    bundle = _bundle_with_frame("frames/00002000.png")
    settings = Settings()
    settings.out_dir = tmp_path / "out"

    out = write_bundle(bundle, settings, frame_sources={"frames/00002000.png": src})

    assert (out / "frames" / "00002000.png").exists()
    data = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["path"] == "frames/00002000.png"


def test_wallclock_chunking_buckets_by_window():
    segs = [_seg(0, 5), _seg(10, 15), _seg(80, 85), _seg(160, 165)]
    chunks = chunk(segs, [], window_s=75.0, duration_s=200.0)
    # boundaries at 0,75,150 -> segments grouped [0,10],[80],[160]
    assert [c.start for c in chunks] == [0.0, 75.0, 150.0]
    assert [len(c.segments) for c in chunks] == [2, 1, 1]


def test_segment_assigned_whole_by_start_never_split():
    # A segment straddling a boundary belongs entirely to the chunk of its start.
    segs = [_seg(70, 80)]  # starts in [0,75)
    chunks = chunk(segs, [], window_s=75.0, duration_s=150.0)
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].segments[0].start == 70


def test_frame_boundaries_used_when_frames_present():
    frames = [Frame(ts=12.0, phash="a"), Frame(ts=30.0, phash="b")]
    segs = [_seg(0, 5), _seg(20, 25), _seg(40, 45)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=60.0)
    # boundaries {0,12,30}: seg@0 -> chunk0; seg@20 -> chunk12; seg@40 -> chunk30
    assert [c.start for c in chunks] == [0.0, 12.0, 30.0]
    assert [s.segments and s.segments[0].start for s in chunks] == [0, 20, 40]
    # frame goes into the chunk it opens
    assert chunks[1].frames[0].ts == 12.0


def test_empty_chunks_dropped():
    frames = [Frame(ts=100.0, phash="a")]  # boundary with nothing before it but seg later
    segs = [_seg(105, 110)]
    chunks = chunk(segs, frames, window_s=75.0, duration_s=120.0)
    assert all(c.segments or c.frames for c in chunks)
