# harvest — Domain Language

Shared vocabulary for harvest, the ingestion front-door for Atlas. This is a **glossary only** —
what each term *means* as a domain fact, not how it's implemented (mechanisms live in the code;
*what/why* lives in [SPEC.md](SPEC.md); the machine contract in [PROTOCOL.md](PROTOCOL.md)).

## Established tracks (anchors)

**Bundle**:
The self-contained per-part deliverable harvest emits (`out/<id>-p<part>/`). Everything below is
either already in the bundle or a candidate to add to it.

**Transcript**:
The original-language, timeline-aligned speech track — the authoritative content of a video.
Highest authority in the bundle.

**Danmaku track**:
A faithful, verbatim MIRROR of the scrolling audience comments (弹幕) that overlay a bilibili
video, organized into content-time windows. It is crowd expression (memes, jokes, sarcasm, often
factually wrong) and carries the **lowest authority** in the bundle. Opt-in (`--danmaku`),
bilibili-only. Every danmaku — including a **UP主 danmaku** — travels in this one scrolling stream.
_Avoid_: "comments" (that word is reserved for the reply section — see **Comment**), "subtitles".

**high_like (高赞)**:
bilibili's own platform-promoted flag on an individual danmaku (peer-elevated, shown with 👍 in
the client). The one danmaku signal that is a **platform fact of higher authority than the
surrounding mirror**, not crowd-opinion-to-be-doubted.

**Authority ranking**:
The trust order harvest records so Atlas can weigh competing signals: transcript (human-sub >
whisper > auto-sub) ≫ everything crowd-sourced. Platform facts (like `high_like`) and uploader-
authored content are carve-outs that rank above the crowd mirror.

**Frame caption**:
The visual note harvest extracts per kept frame. Has two distinct halves: the **OCR** (verbatim
on-screen text) and the **visual description** (a prose description of figures/diagrams/scene/
layout). Both are produced today by one vision-model call.
_Avoid_: "analyze" (say caption); conflating "OCR" (text only) with the whole caption.

## Uploader-initiated interactions

Umbrella (by *intent*, not by mechanism) for signals the **uploader (UP主)** injects into the
viewing experience. The two members below are mechanically unrelated — one is an ordinary danmaku,
the other is not danmaku at all — and share only that the UP主 authored them, giving them higher
authority than organic crowd expression.

**UP主 danmaku** (uploader danmaku):
An ordinary danmaku that happens to be posted by the video's own uploader; the bilibili client
marks it with a solid pink "UP主" pill prefixed to the comment body. It rides the **Danmaku track**
like any other danmaku — a per-danmaku property, orthogonal to `high_like` (one danmaku can be
both; the only two prefix pills the client shows are 👍 and UP主).
_Avoid_: "official danmaku", "pinned danmaku"; do NOT confuse with **Command danmaku**.

**Command danmaku** (互动弹幕 / interactive danmaku):
Structured interactive widgets the uploader places on the timeline — **not** scrolling text and
**not** part of the **Danmaku track**. A separate class with its own acquisition. **On-screen
poll** and **star grading** are the two kinds relevant here.

**On-screen poll** (投票):
A command danmaku presenting a question with discrete options that viewers click. The question is
uploader content; the tallies are crowd response.

**Star grading** (评分 / 打分):
A special on-screen poll where viewers pick a number 1–5 rating the video (or a question framed by
the uploader). Meaning is loose and density-driven — often just "5" spam — so the raw numbers are
**noise without the uploader's framing question** for context.
_Avoid_: "review", "score" (bare).

## Comments

**Comment** (评论 / reply):
A post in the video's **reply section** (the discussion below the video), as opposed to a danmaku
overlaid on the timeline. Considered/substantive relative to danmaku, and **not timeline-aligned**
(pinned to the video as a whole, not to a second of playback).
_Avoid_: "danmaku"; "reply" as a bare synonym when precision matters.

**Comment thread**:
A root comment plus its nested replies. The **thread starter** is the root comment; the rest are
its replies.

**Top comment** (高赞评论):
A comment bilibili surfaces as highly-upvoted — the reply-section analogue of `high_like`.

## Vision

**Vision stage**:
The frame pipeline: download video → sample frames → dedup → produce a **Frame caption** per kept
frame. Today it is all-or-nothing (`--no-vision` skips the whole stage) and tuned for lecture
slides.

**OCR-only**:
A candidate reduced mode that produces only the OCR half of a **Frame caption**, dropping the
visual description. A *shape/cost* lever, not a separate capability.

**Burned-in caption** (硬字幕 / hard-sub):
Speech-subtitle text etched into the video image itself (common on bilibili across genres),
distinct from a selectable subtitle track or the **Transcript**. Because it is *pixels*, the
vision stage's OCR half reads it — making that text redundant with the transcript.
_Avoid_: "subtitle" (that implies a selectable track), "caption" (overloaded with frame caption).
