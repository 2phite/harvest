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
