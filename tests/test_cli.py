from bili_tool.cli import apply_overrides, parse_args
from bili_tool.config import Settings


def _settings():
    s = Settings()
    return s


def test_dedup_threshold_overrides_phash_setting():
    args = parse_args(["https://b/video/BV1", "--dedup-threshold", "16"])
    s = _settings()
    warnings = apply_overrides(s, args)
    assert s.phash_dedup_threshold == 16
    assert warnings == []


def test_scene_threshold_is_deprecated_noop_with_warning():
    args = parse_args(["https://b/video/BV1", "--scene-threshold", "27"])
    s = _settings()
    before = s.phash_dedup_threshold
    warnings = apply_overrides(s, args)
    # retired: it must NOT change any live lever, and it must warn.
    assert s.phash_dedup_threshold == before
    assert any("scene-threshold" in w for w in warnings)


def test_no_overrides_leaves_defaults_and_is_silent():
    args = parse_args(["https://b/video/BV1"])
    s = _settings()
    warnings = apply_overrides(s, args)
    assert warnings == []
    assert s.phash_dedup_threshold == Settings().phash_dedup_threshold


def test_out_override_sets_out_dir():
    from pathlib import Path

    args = parse_args(["https://b/video/BV1", "--out", "somewhere"])
    s = _settings()
    apply_overrides(s, args)
    assert s.out_dir == Path("somewhere")
