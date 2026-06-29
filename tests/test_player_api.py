from bili_tool.player_api import cid_for_part, select_zh_subtitle


def test_cid_for_part_matches_page_number():
    view = {"aid": 1, "cid": 100, "pages": [
        {"page": 1, "cid": 100}, {"page": 2, "cid": 200}, {"page": 3, "cid": 300}]}
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_falls_back_to_index_when_no_page_field():
    # If entries lack a `page` field, positional index is the backstop.
    view = {"aid": 1, "pages": [{"cid": 100}, {"cid": 200}]}
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_single_page_uses_top_level_cid():
    view = {"aid": 1, "cid": 555, "pages": []}
    assert cid_for_part(view, 1) == 555


def test_cid_for_part_out_of_range_is_none():
    view = {"aid": 1, "pages": [{"page": 1, "cid": 100}]}
    assert cid_for_part(view, 9) is None


def test_select_zh_prefers_original_transcription_over_translation():
    subs = [
        {"lan": "ai-en", "ai_type": 1, "subtitle_url": "//x/en"},
        {"lan": "ai-zh", "ai_type": 0, "subtitle_url": "//x/zh"},
        {"lan": "ai-ja", "ai_type": 1, "subtitle_url": "//x/ja"},
    ]
    pick = select_zh_subtitle(subs)
    assert pick["lan"] == "ai-zh"


def test_select_zh_prefers_human_zh_keys_in_order():
    subs = [
        {"lan": "zh-CN", "ai_type": 0},
        {"lan": "zh-Hans", "ai_type": 0},
    ]
    assert select_zh_subtitle(subs)["lan"] == "zh-Hans"


def test_select_zh_none_when_only_foreign_tracks():
    subs = [{"lan": "ai-en", "ai_type": 1}, {"lan": "ai-ja", "ai_type": 1}]
    assert select_zh_subtitle(subs) is None
