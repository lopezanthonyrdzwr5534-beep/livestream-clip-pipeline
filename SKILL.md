---
name: livestream-clip-pipeline
description: Livestream clip pipeline — end-to-end: select high-value clips from an SRT, cut them from the original recording by timestamp (FFmpeg), and remove breaths/silence (auto-editor) in one pass. Use when the user provides a livestream/podcast SRT + original video and asks to cut finished clips or 直播切片. On first use it interviews the user for clip-selection criteria (选段标准), persists them in selection-profile.md, then reuses them on every later run.
argument-hint: <直播SRT路径> <原片路径>
---

# Livestream Clip Pipeline

## Overview

Turn one recording + its SRT into finished clips: **select → cut → trim**.
Selection follows the user's saved profile; cutting uses FFmpeg (frame-accurate
by timestamp); breath/silence removal uses auto-editor. `scripts/pipeline.py`
runs cut+trim deterministically from a manifest.

Dependencies: Python 3, `auto-editor` (`pip install auto-editor`), FFmpeg
(e.g. `winget install Gyan.FFmpeg`). Both are located automatically by the
script (PATH, pip --user Scripts, winget package dirs) or pass `--ae`.

## Profile gate (run first, every invocation)

Clip-selection criteria and rendering defaults live in `selection-profile.md`
next to this SKILL.md.

- File exists → read it and apply it as the authority for Step 1 and the
  parameters. **Never re-interview or alter criteria while a profile exists** —
  it encodes the user's established standard. Only edit it when the user
  explicitly asks to change something.
- File missing → do the first-use interview (below) and write the file BEFORE
  selecting anything.
- User asks to change criteria/naming/speed/etc. at any time → update
  `selection-profile.md` first, then proceed; confirm the changed lines back.

### First-use interview

Ask at most 3 questions, each with a sensible default so the user can accept
quickly (recommend a Chinese-language interview for Chinese livestreams):

1. **内容领域**: what kind of livestream is this and what are the clips for
   (e.g. consultation → short-video marketing; lecture → knowledge-point
   slices; podcast → quote clips).
2. **好切片标准** (multi-option + free text): what to prioritize (real
   cases, concrete numbers, emotional conflict, contrarian hooks, actionable
   advice, complete storylines) and what to explicitly reject (hard selling,
   small talk, half-finished topics…).
3. **单片时长与数量**: target span per clip (e.g. 3–5 / 3–10 / unlimited)
   and how many clips per run (default 5–9).

Then confirm or default the rendering params: speed 1.0×, silence margin 0.2s,
naming `NN-标题短语.mp4`, output `<YYYYMMDD>切片/`. If the user has a pricing
convention, record it; otherwise omit any price column from reports.

### selection-profile.md format

Write exactly these sections (concise bullets, user's words where given):

```markdown
# Clip Pipeline · Profile
确立: <date> ｜ 最近更新: <date> ｜ 场景: <domain/use>

## 选段标准
- 优先: ...
- 拒绝: ...
- 单片时长: ... ｜ 每次条数: ...
## 标题与命名
- 标题风格: ... ｜ 文件命名: NN-标题短语.mp4
## 渲染参数
- 速度: 1.0× ｜ margin: 0.2s ｜ threshold: 默认
- 输出目录: <YYYYMMDD>切片/（源片旁或用户指定）
## 报价口径（可选，无则删节）
```

The profile is personal config — keep it out of version control (see .gitignore).

## Workflow

### Step 0 — Inputs & alignment check

Need the **SRT file** and the **original recording**. Before cutting, verify:
the SRT's max timestamp ≈ the video's duration and both start at 0 (parse the
SRT's last timecode; read video duration via `auto-editor info FILE` or
ffprobe). If the livestream spans several video files, confirm which file the
SRT's timeline refers to — wrong alignment silently cuts the wrong scene.

### Step 1 — Select clips per profile

Work from a compact transcript (cue index + text, one per line; strip timecode
lines). If a `selecting-livestream-clips` skill is installed, use its
`srt_clip_tool.py extract` helper for every number. Otherwise take each clip's
start/end timecodes verbatim from the SRT block boundaries (first cue's start,
last cue's end) and compute spans in code — never by mental arithmetic.

Selection rules: single-topic continuous cue ranges; no substantial overlap
between clips; apply the profile's 优先/拒绝 criteria; rank by value; don't pad
with weak candidates. Titles follow the profile's style. For transcripts over
~2 hours, delegate the full read to a subagent holding the profile + output
contract, and keep only its ranked table in the main context.

### Step 1a — Segments the host asks for on air (always deliver them)

Streamers sometimes break mid-story and address the editor directly: "this part,
editor, cut it out for me", "put a marker here", "I'll turn this into a clip".
Those passages are **delivered unconditionally** — they outrank the duration band,
the clip count and the no-overlap rule, because they are the source's own editorial
intent.

- Scan the whole transcript for the request wording first and list **every** hit.
  One stream typically contains two or three; stopping at the first miss is a
  delivery failure.
- Filter noise: words like "marker/standard/target" appear in ordinary speech
  (grading standards, marking a student's work, "cutting nails"). Read the three
  cues around each hit before deciding.
- **The request line is not the start of the segment.** When the host says "that
  part just now", the passage began tens of seconds or minutes earlier: walk back
  to the topic's real first line (skip a false start that was interrupted by
  another question and then re-spoken) and forward to the topic's own closing
  line, not the next question.
- Cut profanity inside such a segment with multi-range keep-cut anyway; short as
  it may be, still deliver it.

### Step 1b — Cut points come from meaning, not from silence

Splitting blocks at gaps >4s is a cheap way to find candidate ranges — and a bad
way to place a cut. Hosts pause mid-topic (to read the chat, drink, think), so an
acoustic boundary often sits inside a sentence.

- Place the cut on a **topic-turn line** ("now let me talk about the second
  year…", "next question", "as for the fourth year…"), and keep the topic's own
  wrap-up lines with it — a summary or punch that follows a long pause is still
  part of the previous topic.
- Any boundary you invent yourself (splitting a long block, merging two, tightening
  a head or tail) gets the **same verification as delegated candidates**: print the
  three cues before and after each edge. Skipping this produced clips where one
  sentence's first half ended clip A and its second half opened clip B.
- When a long block must be split for length, say so in the report ("these two
  clips join back into one complete X") — let the editor decide, don't silently
  trade a complete story for a duration rule.
- Beware off-by-one in gap scans: a gap between cue N and N+1 starts the new block
  at **N+1**.

### Step 1c — Seven admission checks before a candidate enters the manifest

Fail any one and you fix the boundary or drop the candidate:

1. The opening cue is the topic's **first** line — read the two cues before it; if
   the line before is the hook that announces the story ("let me tell you
   something"), include it.
2. Opening cue, title and one-line summary all describe **the same thing**. If the
   summary describes material from the middle or end of the range, the start is
   too early and the first minute belongs to a different question.
3. The closing cue is a **complete** line that ends the topic (summary, punchline,
   challenge) — never a bare "mm"/"yeah" or a half clause; read the two cues after it.
4. Duration fits the profile band after budgeting **`final ≈ span × 0.7`** (see Step 3).
5. The title contains an anchor that can be found verbatim in the subtitles
   (a number, a company name, a quoted phrase), and that anchor lands within the
   first 45% of the clip. Grep for it after writing the title.
6. No time overlap with already-selected clips or with Step 1a segments.
7. Profanity / platform-sensitive wording inside the span has been located
   (grep the range) and either removed as whole cues or the clip dropped.

### Step 1d — Rules for delegating the long read

Handing a 2-hour transcript to a subagent works, but its output is a lead, not a
decision:

- Ask for **cue indices**; recompute every timestamp, span and duration in the
  main line. Subagent arithmetic is unreliable (observed: a 9:08 range reported as
  5:4, and impossible totals).
- Ask for a one-line summary per candidate and use it to **cross-check the start**
  (rule 2 above) — the summary often betrays that the range begins too early.
- Re-read in source any "this is a digression, drop it" suggestion: one such tip
  would have deleted the most quotable line of the whole clip.
- Verify profanity line numbers it reports.

### Step 2 — Manifest

TSV, one **range** per line (chronological order is fine; the rank lives in the
name):

```text
00:02:38,766	00:07:18,733	06-报名不学到期我不管
```

start/end = exact SRT cue times (comma or dot milliseconds); name = no
extension. `#` lines ignored.

**Multi-range keep-cut**: repeated lines carrying the **same name** are cut
separately in manifest order and concatenated into one finished clip — the way
to express a refined edit that keeps several spans of a single story while
dropping the weak parts in between. List the ranges chronologically and repeat
the name string exactly; the number of clips equals the number of distinct
names.

### Step 2b — Removing spoken junk (whole-cue, text-driven)

Silence trimming cannot remove **said** junk — greetings, restated sentences,
self-references,求关注 lines — they are speech, so auto-editor keeps them. What to
remove is defined by the profile; the mechanism is a keep-range edit:

1. List the cues the clip covers (index / start / end / text) and mark the ones the
   profile's cleanup section calls junk. **If the profile doesn't say what to drop,
   skip this step — never invent criteria.**
2. Cut only on **whole-cue boundaries**: drop the marked cue's `[start, end]` out of
   the kept range. When the junk sits between kept cues, the clip becomes two
   same-name ranges.
3. Endpoints always come from the **neighbouring kept cues**, never from the deleted
   cue's own timecode — that leaves its head or tail in the picture, which matters
   most on exports where adjacent cues touch exactly.
4. Leave single filler particles (and anything you're unsure about) to the editor's
   own NLE pass; chasing them produces a clip with a cut every second.
5. Ship a "removed cues" list (index + timecode + text) so the editor can check and
   continue from it.

### Step 3 — Run the pipeline

```text
python "<skill-dir>/scripts/pipeline.py" \
    --src "REC.mp4" --manifest "clips.tsv" --outdir "OUTDIR" \
    [--margin 0.2s] [--edit "audio:threshold=0.005"] [--fps 30] [--keep-raw] [--dry-run]
```

Per clip: FFmpeg cut [start→end] (input-seek + re-encode, frame-accurate) →
auto-editor silence removal → `OUTDIR/<name>.mp4`, printing span/raw/final
durations per clip. Always `--dry-run` first; then run real batches as a
background task for long sources and monitor the output directory. Choppy /
words clipped → raise `--margin` (e.g. 0.4s); breaths still audible → raise the
threshold via `--edit`.

**Duration model.** Silence trimming eats 6–40% of a span depending on how much the
host pauses: measured across three streams the ratio `final / span` came out 0.72,
0.69 and 0.84, with dialogue/phone-in passages near 0.94 and monologue that keeps
reading chat messages down near 0.6. Plan with **`final ≈ span × 0.7`** — a
conservative estimate only ever buys a slightly longer clip, never a too-short one.
Cut density is a useful cross-check: roughly one cut every 1.6–2.5 s of finished
video is normal, so a high removal ratio at normal density means long pauses, not
over-cutting.

Black-frame defences are built in — all three came from real 60fps/HEVC
livestream sources:

- Cuts are normalized to `--fps 30` (default): auto-editor v29 inserts black
  frames at its own cut points when the source is 60fps.
- A preroll seek absorbs the black head that long-GOP HEVC/TS files produce when
  seeking straight to a keyframe-remote timestamp.
- After trimming, each finished clip is scanned with `blackdetect=d=0.02`; any
  black lying within 1s of either edge is cut away and the clip re-encoded in
  place (the printed final duration reflects the fix).

Suspect a clip starts or ends on black → check it yourself with
`ffmpeg -i CLIP -vf blackdetect=d=0.02 -f null -` rather than eyeballing frames.

### Step 4 — Verify & report

Every clip must exist with `final < raw`. If final ≈ raw, that span had no
detectable silence — say so, never claim a trim. Report a ranked table:
file link | 星级 | span | raw | final | removed (+ price tier only if the
profile defines one). Flag removal ratios over ~30% for a spot-check, and flag
any clip the pipeline reported as black-frame-fixed for a quick watch.

Two gates before you call it delivered:

```text
python "<skill-dir>/scripts/audit_bounds.py" --dump <transcript.tsv> --manifest clips.tsv[,clips_fix.tsv]
```

- **Count check**: the number of Step 1a requests found in the transcript must equal
  the number of host-flagged clips delivered. If they differ, either cut the missing
  one or state in the report why it wasn't delivered — never drop it silently.
- **Boundary audit**: run the script above (read-only) and actually read the four
  lines it prints per clip. It flags `START-MIDSENTENCE` (the opening cue looks like
  a carry-over half sentence), `END-CLIPPED` (the closing cue isn't finished) and
  `TITLE-LATE` / `TITLE-ANCHOR` (the title's anchor appears late in the clip, or not
  at all). It is a tripwire, not a judge: expect the occasional false positive and
  resolve it by reading, and expect it to catch what formal checks missed — that is
  exactly why it exists. Pass several manifests and, per clip name, the later file wins.

## Resources

- `scripts/pipeline.py` — manifest-driven cut (FFmpeg, multi-range concat) +
  silence removal (auto-editor) + edge black-frame auto-fix + duration report;
  UTF-8 one-liners per clip; auto-locates both tools.
- `scripts/audit_bounds.py` — read-only pre-delivery boundary audit (opening/closing
  cue lines plus START-MIDSENTENCE / END-CLIPPED / TITLE-LATE flags).
- `selection-profile.md` — created by the first-use interview; afterwards it
  is the authority for criteria and defaults.
