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

### Step 2 — Manifest

TSV, one clip per line (chronological order is fine; the rank lives in the name):

```text
00:02:38,766	00:07:18,733	06-报名不学到期我不管
```

start/end = exact SRT cue times (comma or dot milliseconds); name = no
extension. `#` lines ignored.

### Step 3 — Run the pipeline

```text
python "<skill-dir>/scripts/pipeline.py" \
    --src "REC.mp4" --manifest "clips.tsv" --outdir "OUTDIR" \
    [--margin 0.2s] [--edit "audio:threshold=0.005"] [--keep-raw] [--dry-run]
```

Per clip: FFmpeg cut [start→end] (input-seek + re-encode, frame-accurate) →
auto-editor silence removal → `OUTDIR/<name>.mp4`, printing span/raw/final
durations per clip. Always `--dry-run` first; then run real batches as a
background task for long sources and monitor the output directory. Choppy /
words clipped → raise `--margin` (e.g. 0.4s); breaths still audible → raise the
threshold via `--edit`.

### Step 4 — Verify & report

Every clip must exist with `final < raw`. If final ≈ raw, that span had no
detectable silence — say so, never claim a trim. Report a ranked table:
file link | 星级 | span | raw | final | removed (+ price tier only if the
profile defines one). Flag removal ratios over ~30% for a spot-check.

## Resources

- `scripts/pipeline.py` — manifest-driven cut (FFmpeg) + silence removal
  (auto-editor) + duration report; UTF-8 one-liners per clip; auto-locates
  both tools.
- `selection-profile.md` — created by the first-use interview; afterwards it
  is the authority for criteria and defaults.
