from harvest.vision import _parse, build_prompt
from harvest.schema import VisionConfig


def test_parse_splits_ocr_and_description():
    text = "OCR:\n大家好 AI Agent\nDESCRIPTION:\nA dark slide with a title."
    ocr, caption = _parse(text)
    assert ocr == "大家好 AI Agent"
    assert caption == "A dark slide with a title."


def test_parse_none_values_become_null():
    text = "OCR:\nNONE\nDESCRIPTION:\nNONE"
    ocr, caption = _parse(text)
    assert ocr is None
    assert caption is None


def test_parse_without_description_marker_is_caption_only():
    text = "A plain talking-head frame, no slide text."
    ocr, caption = _parse(text)
    assert ocr is None
    assert caption == "A plain talking-head frame, no slide text."


def test_parse_ocr_present_description_none():
    text = "OCR:\n01 / 69\nDESCRIPTION:\nNONE"
    ocr, caption = _parse(text)
    assert ocr == "01 / 69"
    assert caption is None


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
    monkeypatch.setattr(vision, "_client", lambda *a, **k: None)

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
    monkeypatch.setattr(vision, "_client", lambda *a, **k: None)

    frames = [Frame(ts=1.0, path="frames/f.png", phash="abcd")]
    out = vision.caption_frames(frames, {"frames/f.png": png}, Settings())
    assert out[0].skipped is False
    assert out[0].ocr == "01 / 69"
    assert out[0].caption == "A dark slide."
