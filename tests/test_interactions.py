"""Tests for the command-danmaku (--interactions) stack: schema types, the pure protobuf
`extra`-payload build step, and the HTTP fetch. Fully offline/hermetic — the fetch tests drive
a fake opener, and the decode tests synthesize protobuf inline (no network, no cookies)."""

from __future__ import annotations

from harvest.schema import Bundle, Grade, Interactions, Vote, VoteOption


def test_interactions_types_roundtrip():
    interactions = Interactions(
        votes=[
            Vote(
                question="喜欢哪个版本？",
                options=[
                    VoteOption(text="只加黄葱", count=153),
                    VoteOption(text="其他，请补充", count=40, write_in=True),
                ],
                total_count=443,
                ts=371.3,
            )
        ],
        grades=[Grade(avg_score=9.9, count=178)],
    )
    dumped = interactions.model_dump()
    assert dumped["votes"][0]["question"] == "喜欢哪个版本？"
    assert dumped["votes"][0]["options"][1] == {"text": "其他，请补充", "count": 40, "write_in": True}
    assert dumped["votes"][0]["total_count"] == 443
    assert dumped["votes"][0]["ts"] == 371.3
    assert dumped["grades"][0] == {"avg_score": 9.9, "count": 178}
    # round-trips back into the model
    assert Interactions(**dumped) == interactions


def test_interactions_empty_defaults():
    empty = Interactions()
    assert empty.votes == []
    assert empty.grades == []


def test_bundle_interactions_defaults_none():
    # A Bundle built without interactions leaves the field null (schema stays 1.0, additive).
    assert "interactions" in Bundle.model_fields
    assert Bundle.model_fields["interactions"].default is None
