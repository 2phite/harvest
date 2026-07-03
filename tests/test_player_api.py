import json

import pytest

from harvest.config import Settings
from harvest.player_api import (
    ViewData,
    ViewError,
    ViewPage,
    cid_for_part,
    fetch_view,
    published_at_iso,
    select_zh_subtitle,
)
from harvest.resolve import Canonical


def test_cid_for_part_matches_page_number():
    view = ViewData(aid=1, cid=100, pages=[
        ViewPage(part=1, cid=100), ViewPage(part=2, cid=200), ViewPage(part=3, cid=300)])
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_falls_back_to_index_when_no_page_field():
    # If entries lack a meaningful `part` field, positional index is the backstop.
    view = ViewData(aid=1, pages=[ViewPage(part=0, cid=100), ViewPage(part=0, cid=200)])
    assert cid_for_part(view, 2) == 200


def test_cid_for_part_single_page_uses_top_level_cid():
    view = ViewData(aid=1, cid=555, pages=[])
    assert cid_for_part(view, 1) == 555


def test_cid_for_part_out_of_range_is_none():
    view = ViewData(aid=1, pages=[ViewPage(part=1, cid=100)])
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


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Minimal stand-in for urllib's opener: maps URL -> JSON-serializable payload."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.requested_urls: list[str] = []

    def open(self, url: str, timeout: int = 60):
        self.requested_urls.append(url)
        body = self._responses[url]
        payload = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
        return _FakeResponse(payload)


def _canonical(part: int = 1) -> Canonical:
    return Canonical("bilibili.com", "BV1", part, f"https://b/video/BV1?p={part}")


def _view_url(canonical: Canonical) -> str:
    from harvest.player_api import _API_VIEW

    return _API_VIEW.format(bvid=canonical.id)


def test_fetch_view_parses_full_fixture():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "My Video",
            "desc": "A description.",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pic": "http://i0.hdslb.com/bfs/archive/thumb.jpg",
            "stat": {
                "view": 1000, "danmaku": 50, "like": 200, "coin": 30,
                "favorite": 40, "share": 10, "reply": 20,
            },
            "pages": [
                {"page": 1, "cid": 100, "part": "Part One", "duration": 300},
                {"page": 2, "cid": 200, "part": "Part Two", "duration": 300},
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)

    assert isinstance(view, ViewData)
    assert view.aid == 42
    assert view.cid == 100
    assert view.title == "My Video"
    assert view.desc == "A description."
    assert view.duration == 600
    assert view.owner_mid == 7
    assert view.owner_name == "Uploader"
    assert len(view.pages) == 2
    assert view.pages[0].part == 1
    assert view.pages[0].cid == 100
    assert view.pages[0].title == "Part One"
    assert view.pages[0].duration == 300
    assert view.pages[1].part == 2
    assert view.pages[1].cid == 200
    # exactly one GET for the view endpoint
    assert opener.requested_urls == [_view_url(canonical)]
    # thumbnail + engagement stats, from the SAME response (no second network call)
    assert view.pic == "http://i0.hdslb.com/bfs/archive/thumb.jpg"
    assert view.view_count == 1000
    assert view.danmaku_count == 50
    assert view.like_count == 200
    assert view.coin_count == 30
    assert view.favorite_count == 40
    assert view.share_count == 10
    assert view.reply_count == 20


def test_fetch_view_stat_and_pic_absent_are_none():
    """A response with no `stat`/`pic` keys must degrade to None, not raise."""
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.pic is None
    assert view.view_count is None
    assert view.danmaku_count is None
    assert view.like_count is None
    assert view.coin_count is None
    assert view.favorite_count is None
    assert view.share_count is None
    assert view.reply_count is None


def test_fetch_view_synthesizes_single_page_when_pages_empty():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)

    assert len(view.pages) == 1
    page = view.pages[0]
    assert page.part == 1
    assert page.cid == 555
    assert page.title is None
    assert page.duration == 120


def test_published_at_iso_converts_epoch_to_cst_offset():
    # 2024-06-28T08:00:00Z -> 2024-06-28T16:00:00+08:00 in bilibili's native CST.
    assert published_at_iso(1719561600) == "2024-06-28T16:00:00+08:00"


def test_published_at_iso_none_for_none():
    assert published_at_iso(None) is None


def test_published_at_iso_none_for_zero():
    assert published_at_iso(0) is None


def test_fetch_view_parses_pubdate():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "d",
            "duration": 120,
            "pubdate": 1719561600,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.pubdate == 1719561600


def test_fetch_view_missing_pubdate_is_none():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "d",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.pubdate is None


def test_fetch_view_empty_desc_becomes_none():
    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 1,
            "cid": 555,
            "title": "Solo",
            "desc": "",
            "duration": 120,
            "owner": {"mid": 1, "name": "Solo Uploader"},
            "pages": [{"page": 1, "cid": 555, "part": "Solo", "duration": 120}],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    view = fetch_view(canonical, Settings(), opener=opener)
    assert view.desc is None


def test_fetch_view_raises_view_error_on_malformed_pages_entry():
    """An upstream `pages[]` entry missing `page` makes `ViewPage(part=...)` get `None`,
    which pydantic rejects (`part: int`). That parse failure must surface as `ViewError`
    (chained), never a bare `pydantic.ValidationError` escaping into callers that only
    catch `ViewError` (process_part, _run_probe)."""
    from pydantic import ValidationError

    canonical = _canonical()
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"cid": 100, "part": "Part One", "duration": 300},  # "page" key missing
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})

    with pytest.raises(ViewError) as excinfo:
        fetch_view(canonical, Settings(), opener=opener)

    assert not isinstance(excinfo.value, ValidationError)
    assert excinfo.value.__cause__ is not None


def test_part_segments_returns_none_on_malformed_view_pages_entry():
    """The same malformed response must degrade `part_segments` to None, not raise."""
    from harvest.player_api import part_segments

    canonical = _canonical(part=1)
    payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"cid": 100, "part": "Part One", "duration": 300},  # "page" key missing
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): payload})
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None


def test_fetch_view_raises_view_error_on_nonzero_code():
    canonical = _canonical()
    payload = {"code": -400, "message": "request error"}
    opener = _FakeOpener({_view_url(canonical): payload})
    with pytest.raises(ViewError):
        fetch_view(canonical, Settings(), opener=opener)


def test_cid_for_part_via_view_data_page_number_match():
    view = ViewData(
        aid=1,
        cid=100,
        title=None,
        desc=None,
        duration=10,
        owner_mid=1,
        owner_name="x",
        pages=[
            ViewPage(part=1, cid=100, title=None, duration=5),
            ViewPage(part=2, cid=200, title=None, duration=5),
        ],
    )
    assert cid_for_part(view, 2) == 200


def test_part_segments_returns_none_when_view_missing_aid_and_cid():
    """Malformed view response (missing aid, and a page with cid absent) must degrade to
    None, matching the pre-refactor behavior, not raise a pydantic ValidationError."""
    from harvest.player_api import part_segments

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            # aid intentionally absent
            "title": "Malformed",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": None, "part": "Part One", "duration": 300},
            ],
        },
    }
    opener = _FakeOpener({_view_url(canonical): view_payload})
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None


def test_part_segments_fetches_view_exactly_once():
    """End-to-end: part_segments must resolve cid via a single fetch_view call, not a raw GET."""
    from harvest.player_api import _API_PLAYER, part_segments

    canonical = _canonical(part=2)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42,
            "cid": 100,
            "title": "My Video",
            "desc": "d",
            "duration": 600,
            "owner": {"mid": 7, "name": "Uploader"},
            "pages": [
                {"page": 1, "cid": 100, "part": "Part One", "duration": 300},
                {"page": 2, "cid": 200, "part": "Part Two", "duration": 300},
            ],
        },
    }
    player_url = _API_PLAYER.format(aid=42, cid=200, bvid="BV1")
    player_payload = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    opener = _FakeOpener(
        {
            _view_url(canonical): view_payload,
            player_url: player_payload,
        }
    )
    result = part_segments(canonical, Settings(), opener=opener)
    assert result is None  # no zh subtitle present
    # exactly one GET to the view endpoint
    view_hits = [u for u in opener.requested_urls if u == _view_url(canonical)]
    assert len(view_hits) == 1


def test_part_segments_accepts_prefetched_view_and_skips_fetch_view():
    """Task 4: when `view` is supplied, part_segments must NOT hit the view endpoint at all."""
    from harvest.player_api import _API_PLAYER, ViewData, ViewPage, part_segments

    canonical = _canonical(part=2)
    view = ViewData(
        aid=42,
        cid=100,
        pages=[
            ViewPage(part=1, cid=100, duration=300),
            ViewPage(part=2, cid=200, duration=300),
        ],
    )
    player_url = _API_PLAYER.format(aid=42, cid=200, bvid="BV1")
    player_payload = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
    opener = _FakeOpener({player_url: player_payload})

    result = part_segments(canonical, Settings(), opener=opener, view=view)

    assert result is None  # no zh subtitle present
    assert _view_url(canonical) not in opener.requested_urls
    assert opener.requested_urls == [player_url]


# --- danmaku acquisition (Task 1: census `seg.so`, protobuf) ---


def _seg_url(cid: int, idx: int) -> str:
    from harvest.player_api import _API_DANMAKU_SEG

    return _API_DANMAKU_SEG.format(cid=cid, idx=idx)


# --- minimal protobuf wire-format ENCODER (test-only; builds fake seg.so segment bodies) ---


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _encode_tag(field: int, wiretype: int) -> bytes:
    return _encode_varint((field << 3) | wiretype)


def _encode_elem(*, progress_ms: int, content: str, attr: int = 0) -> bytes:
    buf = bytearray()
    buf += _encode_tag(2, 0) + _encode_varint(progress_ms)
    data = content.encode("utf-8")
    buf += _encode_tag(7, 2) + _encode_varint(len(data)) + data
    if attr:
        buf += _encode_tag(13, 0) + _encode_varint(attr)
    return bytes(buf)


def _encode_seg(elems: list[tuple[int, str, int]]) -> bytes:
    """Build a fake `DmSegMobileReply` body from `(progress_ms, content, attr)` tuples."""
    out = bytearray()
    for ms, text, attr in elems:
        body = _encode_elem(progress_ms=ms, content=text, attr=attr)
        out += _encode_tag(1, 2) + _encode_varint(len(body)) + body
    return bytes(out)


def test_fetch_danmaku_pages_segments_until_empty_and_orders_chronologically():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"},
            "stat": {"danmaku": 4},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    seg1 = _encode_seg([(3000, "second", 4), (1000, "first", 0)])  # unordered within segment
    seg2 = _encode_seg([(5000, "third", 0)])
    opener = _FakeOpener({
        _view_url(canonical): view_payload,
        _seg_url(100, 1): seg1,
        _seg_url(100, 2): seg2,
        _seg_url(100, 3): b"",  # empty segment -> terminates pagination
    })

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.source_total == 4
    assert result.fetched_total == 3
    # sorted ascending by content_ts regardless of within-segment/across-segment arrival order
    assert [r.text for r in result.records] == ["first", "second", "third"]
    assert [r.content_ts for r in result.records] == [1.0, 3.0, 5.0]
    assert result.records[0].high_like is False
    assert result.records[1].high_like is True  # attr=4 -> HighLike bit2
    assert opener.requested_urls == [
        _view_url(canonical), _seg_url(100, 1), _seg_url(100, 2), _seg_url(100, 3),
    ]


def test_fetch_danmaku_terminates_on_non_protobuf_json_error_body():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"}, "stat": {"danmaku": 1},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    seg1 = _encode_seg([(1000, "only", 0)])
    opener = _FakeOpener({
        _view_url(canonical): view_payload,
        _seg_url(100, 1): seg1,
        _seg_url(100, 2): b'{"code":-352,"message":"risk control"}',
    })

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.fetched_total == 1
    assert result.records[0].text == "only"


def test_fetch_danmaku_partial_on_mid_pagination_error(caplog):
    """A URLError partway through pagination keeps what was already gathered, logs a warning,
    and does not raise (mirrors part_segments's "absence degrades gracefully" stance)."""
    import logging
    from urllib.error import URLError

    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 600,
            "owner": {"mid": 7, "name": "U"}, "stat": {"danmaku": 2},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 600}],
        },
    }
    seg1 = _encode_seg([(1000, "first", 0)])

    class _ErroringOpener(_FakeOpener):
        def open(self, url: str, timeout: int = 60):
            if url == _seg_url(100, 2):
                self.requested_urls.append(url)
                raise URLError("boom")
            return super().open(url, timeout)

    opener = _ErroringOpener({_view_url(canonical): view_payload, _seg_url(100, 1): seg1})

    with caplog.at_level(logging.WARNING):
        result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.fetched_total == 1
    assert result.records[0].text == "first"
    assert result.source_total == 2
    assert any("danmaku" in rec.message.lower() for rec in caplog.records)


def test_fetch_danmaku_accepts_prefetched_view_and_skips_view_get():
    """Task 4: when `view` is supplied, fetch_danmaku must NOT hit the view endpoint at all."""
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view = ViewData(aid=42, cid=100, danmaku_count=5, pages=[ViewPage(part=1, cid=100)])
    seg1 = _encode_seg([(1000, "a", 0)])
    opener = _FakeOpener({_seg_url(100, 1): seg1, _seg_url(100, 2): b""})

    result = fetch_danmaku(canonical, Settings(), opener=opener, view=view)

    assert _view_url(canonical) not in opener.requested_urls
    assert opener.requested_urls == [_seg_url(100, 1), _seg_url(100, 2)]
    assert result.source_total == 5
    assert result.fetched_total == 1


def test_fetch_danmaku_no_cid_returns_empty_result_not_raise():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=9)  # out of range -> no cid
    view = ViewData(aid=42, cid=100, danmaku_count=5, pages=[ViewPage(part=1, cid=100)])
    opener = _FakeOpener({})

    result = fetch_danmaku(canonical, Settings(), opener=opener, view=view)

    assert result.records == []
    assert result.fetched_total == 0
    assert result.source_total == 5
    assert opener.requested_urls == []


def test_fetch_danmaku_view_error_returns_empty_result_not_raise():
    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    opener = _FakeOpener({_view_url(canonical): {"code": -400, "message": "nope"}})

    result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.records == []
    assert result.fetched_total == 0
    assert result.source_total is None


def test_fetch_danmaku_no_warning_on_clean_past_end_termination(caplog):
    """A 304 (or any URLError) on the segment immediately past the last expected segment (per
    `ceil(view.duration / 360)`) is the normal end-of-data terminator bilibili uses -- it must
    NOT log a "stopped early" warning, since nothing was actually truncated."""
    import logging
    from urllib.error import HTTPError

    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 400,
            "owner": {"mid": 7, "name": "U"}, "stat": {"danmaku": 2},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 400}],
        },
    }
    seg1 = _encode_seg([(1000, "first", 0)])
    seg2 = _encode_seg([(2000, "second", 0)])

    class _PastEndOpener(_FakeOpener):
        def open(self, url: str, timeout: int = 60):
            if url == _seg_url(100, 3):
                self.requested_urls.append(url)
                raise HTTPError(url, 304, "Not Modified", {}, None)
            return super().open(url, timeout)

    opener = _PastEndOpener({
        _view_url(canonical): view_payload,
        _seg_url(100, 1): seg1,
        _seg_url(100, 2): seg2,
    })

    with caplog.at_level(logging.WARNING):
        result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.fetched_total == 2
    assert [r.text for r in result.records] == ["first", "second"]
    assert not any("stopped early" in rec.message.lower() for rec in caplog.records)


def test_fetch_danmaku_warns_on_genuine_mid_stream_truncation(caplog):
    """A URLError that hits well before the expected last segment (per
    `ceil(view.duration / 360)`) is a genuine mid-stream failure, not the expected
    past-the-end terminator -- the "stopped early" warning must still fire."""
    import logging
    from urllib.error import HTTPError

    from harvest.player_api import fetch_danmaku

    canonical = _canonical(part=1)
    view_payload = {
        "code": 0,
        "data": {
            "aid": 42, "cid": 100, "title": "T", "desc": "d", "duration": 1904,
            "owner": {"mid": 7, "name": "U"}, "stat": {"danmaku": 1},
            "pages": [{"page": 1, "cid": 100, "part": "P1", "duration": 1904}],
        },
    }
    seg1 = _encode_seg([(1000, "first", 0)])

    class _MidStreamOpener(_FakeOpener):
        def open(self, url: str, timeout: int = 60):
            if url == _seg_url(100, 2):
                self.requested_urls.append(url)
                raise HTTPError(url, 304, "Not Modified", {}, None)
            return super().open(url, timeout)

    opener = _MidStreamOpener({_view_url(canonical): view_payload, _seg_url(100, 1): seg1})

    with caplog.at_level(logging.WARNING):
        result = fetch_danmaku(canonical, Settings(), opener=opener)

    assert result.fetched_total == 1
    assert result.records[0].text == "first"
    assert any("stopped early" in rec.message.lower() for rec in caplog.records)


@pytest.mark.live
def test_live_fetch_danmaku_census_does_not_truncate_multi_segment_video():
    """Real network + cookies: a long (>360s, i.e. multi-segment) public bilibili video. Each
    `seg.so` segment spans ~360s of content-time, so the pagination loop should walk roughly
    `ceil(duration_s / 360)` non-empty segments before hitting the terminating empty one -- if
    the loop's termination condition were wrong (e.g. treating a transient empty/short segment
    as final), `segments_fetched` would fall short of that. Excluded by default
    (-m 'not live'); run explicitly with `-m live` against a real, sufficiently long video."""
    import math

    from harvest.player_api import _opener, fetch_danmaku
    from harvest.resolve import resolve

    settings = Settings.load()
    # A long-form upload -- swap for any known multi-segment (>360s) public BV if this one
    # disappears/changes (bilibili content mutates over time; the check below skips gracefully
    # rather than failing if it's since become single-segment).
    canonical = resolve("https://www.bilibili.com/video/BV11T6UBVEuV")
    real_opener = _opener(settings)
    seg_requests: list[str] = []

    class _CountingOpener:
        def open(self, url: str, timeout: int = 60):
            if "seg.so" in url:
                seg_requests.append(url)
            return real_opener.open(url, timeout=timeout)

    counting = _CountingOpener()
    view = fetch_view(canonical, settings, opener=counting)
    if not (view.duration and view.duration > 360):
        pytest.skip(
            "fixture video is no longer multi-segment (duration_s > 360); "
            "bilibili content mutates over time -- swap in a new long-form BV"
        )

    result = fetch_danmaku(canonical, settings, opener=counting, view=view)

    expected_segments = math.ceil(view.duration / 360)
    segments_fetched = len(seg_requests) - 1  # last request is the empty pagination-terminator
    assert segments_fetched >= expected_segments, (
        f"pagination stopped after {segments_fetched} segments but a {view.duration}s video "
        f"should span >= {expected_segments} -- termination condition may be truncating early"
    )
    assert result.fetched_total > 0

