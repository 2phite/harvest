import types

from harvest.config import Settings
from harvest.frames import dedup_phashes, hamming
from harvest.providers.base import Canonical


def test_hamming_identical_is_zero():
    assert hamming("ffff0000ffff0000", "ffff0000ffff0000") == 0


def test_hamming_counts_differing_bits():
    # 0x0 vs 0xf differ in 4 bits; rest identical.
    assert hamming("0000000000000000", "000000000000000f") == 4


def test_dedup_collapses_near_duplicate_run():
    # Three near-identical frames (same slide) then a clearly different one.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000001"),  # 1 bit off -> same slide
        (8.0, "0000000000000003"),  # 2 bits off the first kept -> still same slide
        (12.0, "ffffffffffffffff"),  # totally different -> new slide
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_keeps_distinct_slides():
    items = [
        (0.0, "0000000000000000"),
        (4.0, "ffffffffffffffff"),
        (8.0, "0f0f0f0f0f0f0f0f"),
    ]
    kept = dedup_phashes(items, threshold=5)
    assert len(kept) == 3


def test_dedup_compares_against_last_kept_not_last_seen():
    # Gradual drift: each step is within threshold of its predecessor, but cumulatively far.
    # Comparing against the last KEPT frame must eventually trigger a new keep.
    items = [
        (0.0, "0000000000000000"),
        (4.0, "0000000000000003"),   # 2 from kept[0] -> drop
        (8.0, "000000000000000f"),   # 4 from kept[0] -> drop
        (12.0, "00000000000000ff"),  # 8 from kept[0] -> keep
    ]
    kept = dedup_phashes(items, threshold=5)
    assert [ts for ts, _ in kept] == [0.0, 12.0]


def test_dedup_empty():
    assert dedup_phashes([], threshold=5) == []


# --- issue #9: video download must not hand YouTube to aria2c (same class as #3) ---------------


class _FakeYDL:
    """Records the opts it was constructed with and writes the target file on extract_info."""

    captured: dict = {}

    def __init__(self, opts):
        _FakeYDL.captured = opts
        self._info = opts.pop("_test_info", {})
        self._writes = opts.pop("_test_writes", None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        if self._writes is not None:
            self._writes()
        return self._info


def _fake_ydl_factory(monkeypatch, info, writes=None):
    from harvest import frames as F

    def make(opts):
        opts["_test_info"] = info
        opts["_test_writes"] = writes
        return _FakeYDL(opts)

    monkeypatch.setattr(F, "yt_dlp", types.SimpleNamespace(YoutubeDL=make))


def _prep(tmp_path, key):
    vdir = tmp_path / "video"
    vdir.mkdir(parents=True)
    return vdir / f"{key}.mp4"


def test_download_video_youtube_uses_native_downloader(tmp_path, monkeypatch):
    from harvest import frames as F

    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    target = _prep(tmp_path, "youtube.com_F8X9_Dp3ZUk_1")
    canon = Canonical("youtube.com", "F8X9_Dp3ZUk", 1,
                      "https://www.youtube.com/watch?v=F8X9_Dp3ZUk")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    F.download_video(canon, s)
    assert "external_downloader" not in _FakeYDL.captured


def test_download_video_bilibili_keeps_aria2c(tmp_path, monkeypatch):
    from harvest import frames as F

    s = Settings(cache_dir=tmp_path, aria2c_path="C:/aria2c.exe")
    target = _prep(tmp_path, "bilibili.com_BV1_1")
    canon = Canonical("bilibili.com", "BV1", 1, "https://www.bilibili.com/video/BV1")
    info = {"requested_downloads": [{"filepath": str(target)}]}
    _fake_ydl_factory(monkeypatch, info, lambda: target.write_bytes(b"x"))
    F.download_video(canon, s)
    assert _FakeYDL.captured["external_downloader"] == {"default": "C:/aria2c.exe"}


# --- Task 3: cap_frames uniform-thinning tests ---


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


# --- final-review Finding 1: sample_interval must be folded into the raw-frame cache key -------


def _make_png(path):
    from PIL import Image

    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(path)


def test_extract_frames_reextracts_when_sample_interval_changes(tmp_path, monkeypatch):
    """PROTOCOL.md: frame-selection overrides that differ from defaults must change the raw-frame
    cache key so a changed sample_interval re-extracts instead of silently reusing stale frames at
    mislabeled timestamps (BLOCKER finding)."""
    from harvest import frames as F
    from harvest.providers.base import Canonical

    captured_raw_dirs = []

    def fake_bulk_sample(video_path, interval, raw_dir, ffmpeg):
        captured_raw_dirs.append(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        p = raw_dir / "f_000000.png"
        if not p.exists():
            _make_png(p)
        return [(0.0, p)]

    monkeypatch.setattr(F, "_bulk_sample", fake_bulk_sample)

    canonical = Canonical("bilibili.com", "BVx", 1, "u")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"x")

    s1 = Settings(cache_dir=tmp_path, ffmpeg_path="ffmpeg", sample_interval_s=6.0)
    s2 = Settings(cache_dir=tmp_path, ffmpeg_path="ffmpeg", sample_interval_s=3.0)

    F.extract_frames(canonical, video_path, s1)
    F.extract_frames(canonical, video_path, s1)  # same interval -> stable raw_dir
    F.extract_frames(canonical, video_path, s2)  # different interval -> distinct raw_dir

    assert captured_raw_dirs[0] == captured_raw_dirs[1]
    assert captured_raw_dirs[0] != captured_raw_dirs[2]
