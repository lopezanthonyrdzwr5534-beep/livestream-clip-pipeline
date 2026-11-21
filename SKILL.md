---
name: livestream-clip-pipeline
description: "Two-pass livestream clip pipeline. Pass 1: select high-value ranges from an SRT by cue index, delete whole junk cues, cut and remove breaths/silence with auto-editor at source speed, then package each clip as an editable NLE draft (fixed vertical template, no subtitles) appended to the source project as timeline 02+. Pass 2: after the editor recognises subtitles in the NLE, proofread the text, finalise titles from the corrected subtitles, and place sparse semantic sound effects / visual effects. Use when the user supplies a livestream or podcast recording plus its SRT and asks for finished clips, editable drafts, or 直播切片. On first use it interviews for clip-selection criteria, persists them in selection-profile.md, then reuses them on every later run."
argument-hint: <SRT path> <source recording path> <NLE project dir>
---

# Livestream Clip Pipeline (two passes)

## Overview

One recording + its SRT become **editable drafts inside the editor's own project**,
not standalone exports. The work is split in two, and the order cannot be swapped:

```
source replay (1.0x) + its SRT
  Pass 1 (agent)
    Step 1  select ranges by cue index (seven admission checks)
    Step 2  delete whole junk cues by cue index
    Step 3  make_manifest.py    indices -> manifest.tsv (two hard gates)
    Step 4  pipeline_mp4.py     cut + auto-editor breath removal, material stays 1.0x
    Step 5  build_draft.py      single-segment draft, fixed template, NO subtitle track
    Step 6  install_timeline.py append as timeline 02+ (encrypted, read back)
         |  editor: recognise subtitles on that timeline inside the NLE
  Pass 2 (agent)
    Step 7    proofread subtitle text (typos / terms / pronouns / numerals)
    Step 7.5  finalise titles from the corrected subtitles
    Step 8    sparse semantic sfx / visual effects
    Step 9    acceptance checks
```

Subtitles must be recognised **after** the breaths are cut and the speed is fixed;
recognising them earlier means every cue has to be recomputed. Pass 1 delivers
"clean, packaged, subtitle-free" drafts and nothing else — no effects, no music, and
only placeholder titles.

A standalone one-pass script (`scripts/pipeline.py`, cut + trim straight to finished
MP4) is still shipped for workflows that do not use an NLE. Everything below describes
the two-pass draft workflow, which is the maintained path.

## Environment

- **Python**: an interpreter with `jieba` installed (the subtitle splitter degrades
  without it). Pass it explicitly to every script.
- **ffmpeg / ffprobe** on PATH.
- **jy-draftc** (draft decrypt/encrypt helper). `--jy-dir` must point at the directory
  that **currently** contains `videoeditor.dll`; locate it at run time
  (`find <JianYing-install-root> -maxdepth 2 -iname videoeditor.dll`) instead of trusting
  a version number written in a doc. The NLE's self-update renames the previous version's
  DLLs (`*_d_<hash>`); `scripts/restore_dlls.py` builds a private mirror of the originals
  (see `references/toolchain.md`). Ciphertext is stable across versions, so a mirror keeps
  working after an upgrade.
- **auto-editor**: prefer a user-level install
  (`~/AppData/Roaming/Python/Python3xx/site-packages/auto_editor/bin/auto-editor.exe`).
  Do not let pip reinstall it — that package downloads its ~37MB main program from GitHub
  on first run, which often fails on a direct connection.
- Run the **profile gate** below before anything else.

## Profile gate (run first, every invocation)

Clip-selection criteria and rendering defaults live in `selection-profile.md` next to
this SKILL.md. It is personal config and is kept out of version control.

- File exists → read it and treat it as the authority for Step 1 and the parameters.
  **Never re-interview or alter criteria while a profile exists.** Only edit it when the
  user explicitly asks to change something.
- File missing → do the first-use interview and write the file BEFORE selecting anything.
- User asks to change criteria / naming / speed → update the profile first, then proceed,
  and confirm the changed lines back.

### First-use interview

At most 3 questions, each with a sensible default (interview in the language of the
source material):

1. **Domain**: what kind of stream is this and what are the clips for (consultation →
   short-video marketing; lecture → knowledge points; podcast → quote clips).
2. **What makes a good clip** (multi-option + free text): what to prioritise (real cases,
   concrete numbers, emotional conflict, contrarian hooks, actionable advice, complete
   storylines) and what to reject outright (hard selling, small talk, half-finished topics).
3. **Length and count**: target finished length per clip and how many per run (default 5–9).

Then confirm or default the rendering parameters: material speed 1.0×, playback speed in
the draft (commonly 1.2×), silence margin 0.2s, naming `NN-title-phrase.mp4`, output
`<YYYYMMDD>clips/`. Record a pricing convention only if the user has one; otherwise omit
any price column from reports.

### selection-profile.md format

```markdown
# Clip Pipeline · Profile
established: <date> | updated: <date> | domain: <domain/use>

## Selection criteria
- prefer: ...
- reject: ...
- finished length: ... | clips per run: ...
## Titles and naming
- title style: ... | file naming: NN-title-phrase.mp4
## Rendering
- material speed: 1.0x | draft speed: 1.2x | margin: 0.2s | threshold: default
- output dir: <YYYYMMDD>clips/
## Pricing (optional; delete the section if unused)
```

## Inputs

- The **source replay** at 1.0× (often a 2–3GB HEVC file at **60fps**) and its **SRT**.
- The target NLE project directory (contains `Timelines/project.json`; timeline 01 is the
  full source and is **never modified**).
- One **decrypted reference timeline** to copy the fixed template from.
- The clip-name list, following the profile's naming rule.

**Check alignment first**: the SRT's last timestamp ≈ the video duration, both start at 0,
cue indices are continuous with no timecode going backwards. Some NLE exports are
*wrapped* (the file starts mid-timeline); detect it by looking for a timecode regression
and re-sort by time before selecting. If the stream spans several video files, confirm
which file the SRT refers to — wrong alignment silently cuts the wrong scene.

## Pass 1 — select, cut, package

### Step 0 — Preflight

```bash
python scripts/check_resources.py --content <reference timeline> --jy-draftc <exe> --jy-dir <NLE dir>
```

Require `missing: 0`. Draft JSON stores only paths; a missing mask package turns a
**top-strip blur into a full-screen blur**. Then build or refresh the effect library
(pass 2 needs it):

```bash
python scripts/build_fx_library.py --plain "<decrypted timeline dirs, comma separated>" --out assets/fx_library.json
```

`assets/fx_library.json` is **generated per machine** (it records absolute cache paths) and
is not shipped with the skill.

### Step 1 — Select by cue index, never by mental arithmetic

```bash
python scripts/select_helper.py dump   --srt master.srt --out cues.tsv
python scripts/select_helper.py blocks --srt master.srt --gap 4 --min-dur 180
python scripts/select_helper.py marks  --srt master.srt          # host-requested segments
python scripts/select_helper.py dirty  --srt master.srt --from 0 --to <last second>
python scripts/select_helper.py anchor --title "<title>" --idx-from N --idx-to M
```

Pause blocks only **nominate candidates**; the cut itself is decided by meaning. Write the
selection as `clips_idx.tsv`: `name<TAB>first cue<TAB>last cue`, one line per clip. Several
topics inside one clip are expressed as deleted cues (Step 2), not as extra lines.

For transcripts longer than ~2 hours, delegate the full read to a subagent holding the
profile and the output contract, and keep only its ranked table in the main context
(Step 1d).

### Step 1a — Segments the host asks for on air

Streamers sometimes address the editor mid-story. **The request wording has two opposite
meanings, and you must read the surrounding cues before deciding which one it is:**

| What the host says | What it means | What you do |
|---|---|---|
| "editor, cut this part out for me", "mark this", "I'll make a clip of this" | **deliver it** | highest priority: deliver every such segment, exempt from the length band, the clip count and the no-overlap rule |
| "cut this bit", "don't air this", "skip this part" | **remove it** | excise the whole block from every clip; it must not survive into a draft |

- Scan the whole transcript and list **every** hit before acting. One stream typically has
  two or three; stopping at the first is a delivery failure.
- Filter noise: "marker / standard / target" also occur in ordinary speech (grading
  standards, marking homework, "cutting nails"). Read three cues around each hit.
- **The request line is not the segment's start.** Walk back to the topic's real first line
  (skip a false start that was interrupted and re-spoken) and forward to the topic's own
  closing line, not the next question.
- A "don't air this" block is removed as a whole, including the sentences around it that
  only make sense with it. Profanity inside a *deliver* segment is still cut by whole cue.

### Step 1b — Cut points come from meaning, not from silence

Splitting at gaps > 4s is a cheap way to find candidates and a bad way to place a cut:
hosts pause mid-topic to read chat, drink or think.

- Place the cut on a **topic-turn line** ("now the second year…", "next question"), and keep
  the topic's own wrap-up with it — a summary after a long pause still belongs to the
  previous topic.
- Any boundary you invent (splitting a long block, merging two, tightening an edge) gets the
  **same verification as a delegated candidate**: print three cues before and after each
  edge. Skipping this produced clips where one sentence's first half ended clip A and its
  second half opened clip B.
- Three recurring mistakes, in order of severity:
  1. **Treating a pause as a topic change.** A few seconds of silence, or one line answering
     the chat, is not the end of an argument. Read forward: is the next line continuing the
     same reasoning or opening a new question?
  2. **Splitting one complete argument into two clips.** First half states the problem, second
     half gives the conclusion; together they work, apart the first is "problem with no
     answer" and viewers feel it stops abruptly.
  3. **Dropping a candidate because its boundary is dirty.** The default action for an
     unusable boundary is **merge it back into the neighbouring topic**, not delete it.
     Dropping is only for material with no value at all.
- Two hard signals mean re-cut, not "acceptable deviation": finished length below the
  profile's floor (suspect the topic was not finished — read 5–10 cues past the end), and a
  continuation line right after the end that belongs to the same argument (same subject,
  same conclusion, "so / let me stress again / only these two").
- **Reusing a previous session's ranges requires re-judging the boundaries.** Inherited
  ranges are leads only: after converting the speed factor, re-read 3–5 cues at each edge.
  The old boundary itself may have been wrong.
- Beware off-by-one in gap scans: a gap between cue N and N+1 starts the new block at **N+1**.

### Step 1c — Seven admission checks before a candidate enters the manifest

Fail any one → fix the boundary or drop the candidate:

1. The opening cue is the topic's **first** line; read the two cues before it. If the line
   before is the hook that announces the story, include it and make it the start.
2. Opening cue, title and one-line summary describe **the same thing**. A summary describing
   material from the middle or end means the start is too early.
3. The closing cue is a **complete** line that ends the topic (summary, punchline, challenge)
   — never a bare "mm"/"yeah" or a half clause; read the two cues after it.
4. Finished length fits the profile band after budgeting **`finished ≈ span × 0.6`** (Step 4).
5. The title's hook is actually argued in this clip, and its evidence lands in the **first
   45%**. Titles are a distillation, not a verbatim quote — but every number and fact in a
   title must be findable in the subtitles. Grep for it after writing.
6. No time overlap with other clips or with Step 1a segments.
7. Profanity / platform-sensitive wording inside the span has been located and either removed
   as whole cues or the clip dropped.

### Step 1d — Rules for delegating the long read

A subagent's output is a lead, not a decision:

- Ask for **cue indices**; recompute every timestamp, span and duration in the main line.
  Subagent arithmetic is unreliable (observed: a 9:08 range reported as 5:4).
- Ask for a one-line summary per candidate and use it to cross-check the start (rule 2).
- Re-read in source any "this is a digression, drop it" suggestion — one such tip would have
  deleted the most quotable line of the whole clip.
- Verify the profanity line numbers it reports.

### Step 2 — Remove spoken junk (whole cues, text-driven)

Silence trimming cannot remove **said** junk: greetings, restated openings, self-references,
follow/like pleas, one-line digressions. They are speech, so auto-editor keeps them. Decide
what to drop from the text *before* cutting, and write `delete_idx.txt` (one cue index per
line, with the reason kept alongside in your notes).

- Only whole-cue deletions the profile calls for. Filler particles and in-sentence tics are
  left to the editor's own NLE pass.
- **Do not aim for a perfect first cut.** Better to leave work for the fine cut than to
  shred the timeline chasing one word.
- Endpoints always come from the **neighbouring kept cues**, never from the deleted cue's own
  timecode — otherwise its head or tail stays in the picture.
- Ship the removed-cue list (index + timecode + text + reason) so the editor can check it.

### Step 3 — Manifest, with two hard gates

```bash
python scripts/make_manifest.py --srt master.srt --clips clips_idx.tsv \
    --delete delete_idx.txt --out manifest.tsv \
    --min-final 300 --est-ratio 0.6 --check-tail 5
```

Indices become timecodes, deleted cues are excised, adjacent kept cues merge. Output is
`HH:MM:SS.mmm<TAB>HH:MM:SS.mmm<TAB>name`; repeated names = several kept ranges of one clip.

The script **fails (exit 1)** rather than letting a bad range through:

- `--min-final 300`: estimated finished length (kept × `--est-ratio`) below the floor →
  `[SHORT]`. Suspect an unfinished topic first; read 5–10 cues past the end. Only add
  `--allow-short` once you have confirmed the topic really is that short.
- `--check-tail 5`: prints five cues after every end and **you must read them**. A
  continuation phrase ("let me stress again", "only these two", "so that's why") →
  `[CONTINUATION]`; the default is to merge it into the same topic. `--allow-continuation`
  is a human sign-off that the topic genuinely ends there.

Both escapes are sign-offs, not tuning knobs. Never reuse a previous session's manifest
without re-running these gates.

### Step 4 — Cut, remove breaths, fix the speed in exactly one place

```bash
python scripts/pipeline_mp4.py --src <replay.mp4> --manifest manifest.tsv --outdir out/ \
    --ae <auto-editor.exe> --margin 0.2s --speed 1.0 --fps 30
```

One pass: FFmpeg cuts each kept range (preroll seek + re-encode) → auto-editor removes
silence → edge black frames are trimmed. It prints span / raw / final per clip.

- **Speed lives in one place only.** Keep the material at 1.0× (`--speed 1.0`) and put the
  playback speed in the draft's EDL (`speed: 1.2`), so the editor can dial it back to 1.0 or
  change it to 1.1/1.25 inside the NLE. If the user instead wants "material already at final
  speed", use `--speed 1.2` and set the EDL `speed` back to 1.0. **Never bake the speed into
  the file and also set a speed material** — that is the root cause of waveform drift and a
  frozen last frame while audio keeps playing.
- **`--fps 30` is mandatory on 60fps sources**: auto-editor v29 inserts black frames at its
  own cut points otherwise.
- **Pass ASCII clip names** (e.g. `c01`) to the pipeline: ffmpeg's concat list on Windows
  eats non-ASCII filenames. Rename afterwards.
- Finished (timeline) length ≈ span × **0.6** (breaths ≈ −28%, then 1.2× in the NLE ≈ −17%).
  For a ≥5-minute finished clip, select a span of ≥8:30. Measured `final / span` across three
  streams: 0.72, 0.69, 0.84 — dialogue/phone-in near 0.94, monologue that keeps reading chat
  near 0.6. Plan with the conservative factor; it only ever buys a slightly longer clip.
  Cut density is a cross-check: about one cut per 1.6–2.5s of finished video is normal, so a
  high removal ratio at normal density means long pauses, not over-cutting.
- Black-frame defences, all from real 60fps/HEVC sources: cuts normalized to `--fps 30`; a
  preroll seek absorbs the black head that long-GOP HEVC/TS produces when seeking to a
  keyframe-remote timestamp; after trimming each clip is scanned with `blackdetect=d=0.02`
  and any black within 1s of an edge is cut away and re-encoded (the printed final duration
  reflects the fix). Suspect a black edge → verify with
  `ffmpeg -i CLIP -vf blackdetect=d=0.02 -f null -`, not by eyeballing frames.
- **`--margin` is padding, not trimming**: the cut still lands inside the detected
  silence, and the margin decides how much silence is kept on each side. A 0.2s margin
  therefore keeps every gap shorter than 0.4s intact (measured on one clip: 234 gaps of
  0.25–0.45s). 0.1s and 0.15s were tried on the same clip and preserved **exactly the same
  speech** (4.44 min each) — they only keep less silence. So do not reach for the margin to
  "cut more": it will never swallow words, but the tighter joins can *sound* clipped to the
  creator. **Default 0.2s.**
- Choppy / clipped words → raise `--margin` (0.2–0.4s). Breaths still audible → raise the
  threshold via `--edit "audio:threshold=..."`.
- Long sources are slow: `--dry-run` first, then run the real batch as a background task and
  watch the output directory.

### Step 5 — Build the draft (package only: no subtitles, no effects)

Write one EDL per clip: **single segment**, `speed` per Step 4, `subtitles` empty, `titles`
two lines (placeholders are fine — they are finalised in Step 7.5).

```bash
python scripts/build_draft.py --template <decrypted reference timeline> \
    --edl edl/<name>.json --out draft/<name> --fx-library assets/fx_library.json
python scripts/check_preset.py --template draft/<name> --allow-no-subtitles
```

`build_draft.py` enforces the subtitle style from `assets/preset.json` (used in pass 2) and
does **not** create an empty subtitle track when subtitles are empty. See
`assets/edl.example.json`.

### Step 6 — Install as timeline 02+

```bash
python scripts/install_timeline.py --project <source project> --content draft/<name> \
    --name <clip name> --jy-draftc <exe> --jy-dir <NLE dir> --sort
```

The installed file must be **ciphertext** and pass read-back verification (it prints
`[encrypted, read-back OK]`). One replay = one project; timeline 01 stays untouched; do
**not** create a separate standalone draft. Backups are uniquely named automatically.
Then have the user **fully quit and reopen the NLE** and confirm the timelines are visible
and the top blur covers only the upper strip.

## Pass 2 — after the editor has recognised subtitles

### Step 7 — Proofread the subtitle text

Change only typos, terminology, pronouns and mis-written amounts. **No rewriting, no
removing normal speech habits, no reordering words.**

- Terminology: fix ASR homophones for domain terms (a major's name, a job title), split
  merged school codes (`985211` → `985/211`), a lone `at` → `@`.
- Pronouns by context (a female student → "she"; an object or a city → "it"). Do **not**
  "correct" a gender-neutral plural into a gendered one.
- **Write numbers back into words** in the source language (e.g. Chinese `七八千`, `一万`,
  `三十二万`, `三个月`, `倒数第二`). `digits_to_cn()` only converts a number followed by a
  magnitude or a classifier (2 leading reads "two", 20/32 read "twenty"). Leave untouched:
  school codes, `1v1`, `4A`, platform names, `0offer`, percentages, years/months/cohort
  years, decade labels.
- **Verb reduplication is not a stutter**: "think it over over", "have a look look" style
  repetitions are natural speech and stay. Only collapse repeated single function words
  (`STUTTER_CHARS` + `REDUPLICATE`).

Splitting / merging / zero-gap is done by `rework_subtitles.py` (proofread → merge short
pauses → de-duplicate → split long lines → previous end = next start). Always rebuild from
the **raw recognition copy**, never from an already-reworked track (merging pauses twice
shortens the clip). Verify with `subtitle_audit.py`: coverage ≥98%, one line within the
profile's character limit, repeats ≤2, no stray `at`, weak boundaries within tolerance
(weak-boundary metrics produce false positives on some hosts — resolve by reading).
**Rebuild only the subtitle track; never touch video, titles or effects.**

### Step 7.5 — Finalise titles from the corrected subtitles

A title is a **distillation of the content**, not a subtitle excerpt: it needs a hook (a real
number, a sharp contrast, a contrarian claim, suspense) and need not be verbatim. But
everything it asserts must actually be argued in that clip:

- Read the corrected cues (`final_cues/NN.txt`): at least the first 15 lines, the mid-clip
  conclusion, and every line containing a number.
- Grep each number/fact back to its cue and note the second it appears at. **If you cannot
  find it, you may not write it.**
- Put the hook's evidence in the first 45% of the clip; do not title from an argument that
  only appears at the end.
- No exaggeration, no invented conclusions. A claim like "90% cannot find a job" must be
  checked against which group it was said about.
- Report per clip: kept / changed to what / evidence at which second.
- **Never write titles back into a timeline the editor has already hand-edited.** Deliver the
  text for them to paste, or wait for explicit permission.

### Step 8 — Sound effects and visual effects

Place by what the line *does*, not by the clock. Density **0.3–1.2 per minute** (about 3–5 in
a 6-minute clip), plus 0–2 extra visual effects. The template carries only a few packages;
clone the rest by name from `assets/fx_library.json`.

- An event's `kind` must match the library (`sfx` / `video_effect`). A mismatch is not
  silent: `build_draft.py` lists every bad event and exits 1 before building.
- **`path_exists` in `fx_library.json` is a snapshot, not live state.** The NLE cleans up
  packages it has not used recently. After placing events, run `check_resources.py` against
  the **written-back timeline**; `MISSING [asset] <name>` means the package is gone from this
  machine and the effect will be silently mute. Fix by swapping the asset **on the same
  segment** (keeping timing and track) — never re-run the placement script, which would
  duplicate the whole event table.
- **Keep adjacent sound effects at least 2.5s apart**; closer than that they blur into one
  hit. Fewer is better than muddy.
- **Missing packages can be restored offline**, without re-searching the NLE library.
  `scripts/restore_fx.py` reads the NLE's own resource database
  (`Cache/ressdk_db/<account>/rp.db`, table `http_cache`), finds the original download URL by
  the `resource_id / effect_id / md5` recorded in the draft, downloads, verifies MD5, extracts
  back to `Cache/effect/<effect_id>/<md5>/` and writes `<md5>_modity_time.txt`. Timing and
  placement are untouched.

```bash
python scripts/restore_fx.py --check   --content <timeline dir> --jy-draftc <exe> --jy-dir <NLE dir>
python scripts/restore_fx.py --restore --name <asset name> [<asset name> ...]
```

`--check` splits the result in two: **"recoverable: md5=…"** means rp.db still has a URL and
one command restores it; **"no record / record without URL"** means the asset was delisted and
the editor must pick a similar one in the NLE library. `--name` matches the library key
**exactly** — a short nickname reports "not in library" even when the full name is there.
Quit the NLE completely before running, and restart it afterwards. The same download logic is
in `build_fx_library.py --repair` (library-wide); `restore_fx.py` works per timeline and also
repairs the "files present but `_modity_time.txt` missing" case that the NLE refuses to load.

Two traps when auditing the library:

- Test presence with **`Path(path).exists()`** (the same predicate `check_resources.py` uses):
  sounds are `.mp3` **files**, effects are **directories**, so an `is_dir()` test marks every
  sound as missing. For a directory also check it is non-empty and that a sibling
  `<md5>_modity_time.txt` exists, otherwise the NLE will not load it.
- A download can fail with **403** when the md5 recorded in the draft differs from the newest
  md5 in the resource database (the asset changed version); the old URL is dead and the only
  fix is to pick a similar asset by hand.

### Step 9 — Acceptance

- Timeline 01 unmodified (SHA256 against the copy taken before work started); new timelines
  after it; installed files are ciphertext and read back with matching id and duration.
- Finished length ≈ span × 0.6; `blackdetect=d=0.02` finds nothing across the whole clip;
  sample frames at the head, a cut point, an effect point and the tail.
- Subtitles: one line within the limit, **zero gaps** between adjacent cues (a genuine pause
  >1.0s may keep one), coverage ≥98%, re-joining them reproduces the original sentence,
  numbers written in words, only typos changed.
- `check_resources.py` reports `missing: 0` — **run it again after placing effects, against
  the written-back timeline**. The top blur can only be confirmed **visually inside the NLE**;
  JSON validation and a silent export prove nothing about a mask.
- Effects match the semantics and the density budget; after writing back, read back
  "non-subtitle segment count = original + number of events" to prove they landed.
- Do the first clip end-to-end before starting the rest; keep batches to 6–8 clips.
- Never claim something is visible in the NLE until the user confirms it.

## Fixed template (copy it, do not redesign it)

| Item | Value |
|---|---|
| Canvas | 1080×1920 vertical |
| Colour | every adjustment sub-item at **0.8**, on each video segment |
| Top blur | blur + rectangular mask, height ≈14.6%, centre Y ≈0.70; covers only the upper strip, hiding the source's burned-in title |
| Subtitles | thin Source Han Sans, size **12**, `#FFFFFF`, y ≈ −0.236, shadow 0.9, single line, **≤11 characters** |
| Titles | a heavy display face, white, **size 13** on both lines, y ≈ 0.7446 / 0.6231, **≤9 characters per line** |
| Transitions / stickers / BGM / keyframes | **none** |
| Track order | video → vignette/zoom → blur → titles → audio → subtitles (subtitles above the blur) |

Fonts must exist on the machine. If the reference project uses a different mask (e.g. height
15.1%, centre 0.626), **the reference project wins**. `assets/preset.json` holds these values
and is what `build_draft.py` enforces.

## Deletion rules (pass 1)

Delete whole sentences: pure filler with no information, half-sentence interjections,
stutters and self-corrections, repetition and self-reference, ads and follow pleas,
digressions that do not advance the main line.
Keep: the main argument, key examples, numbers/salary/offer figures, conclusions, memorable
emotional lines. **Delete whole sentences, not individual words; leave in-sentence tics.**

## Semantic mapping for effects

| What the line does | Sound | Visual |
|---|---|---|
| Names an uncomfortable truth / negative conclusion | water drop | vignette (when strong) |
| Lands a conclusion or keyword | subtitle "thud" | none |
| Gives advice / a warning / talks someone out of it | system warning | spotlight |
| Conflict, repeated negation, repeated emphasis | punch / boxing hit | "furious" (when strong) |
| Social death, awkwardness, self-mockery | awkward / awkward-exit | question marks / film noise |
| Topic change, listing points, scene switch | book page turn | none |
| Joke, "I'm out" | comedy "sneaking away" | none |
| Shock / dismay / realisation / doing the maths | variety-show shock / disappointed "deng" / fast calculation | brain scramble / surprise / lightbulb |
| Reveals a point | variety-show "reveal" or a chime | none |
| Complaining, annoyed, overheated | none | "red-hot head" |
| A run of listed points or negative reasons | short raspberry (may repeat 4–6×) | none |
| Plain statement | none | none |

## Don'ts

- **Do not assume `seg["speed"] = 1.0` means 1.0×.** The NLE reads speed from the `speeds`
  material referenced in the segment's `extra_material_refs`, not from `seg.speed`. If the
  template's own 1.2× speed material is copied along, the NLE plays at 1.2 and rewrites the
  source range as `target × 1.2`; when the material is not long enough it runs out —
  **waveform drifts from the audio, worse towards the end, last frame frozen while audio
  continues**. `build_draft.py` strips speed references and deletes orphan speed materials;
  `check_preset.py` compares "speed in the references" against "the timeline ratio" and FAILs
  on a mismatch.
- Do not put subtitles into the pass-1 draft.
- Do not both bake a speed change into the material and set a speed segment in the draft.
- Do not run auto-editor directly on a 60fps source (`--fps 30`).
- Do not hand non-ASCII clip names to ffmpeg's concat list.
- Do not create a standalone draft; append to the source project as timeline 02+.
- Do not run decrypt/encrypt against a project the NLE currently has open; copy
  `Timelines/<id>` first.
- Do not treat "JSON validates" or "the export has audio" as proof that a mask works.
- Do not re-run an event-placement script to fix one asset — swap the asset on that segment.

## Resources

Scripts (`scripts/`):

- `select_helper.py` — dump / blocks / marks / dirty / anchor over an SRT.
- `make_manifest.py` — cue indices → manifest, with the two hard gates.
- `pipeline_mp4.py` — multi-range cut + auto-editor + edge black-frame fix + duration report.
- `pipeline.py` — the older standalone one-pass cut+trim to finished MP4 (no NLE).
- `build_draft.py`, `check_preset.py` — package an EDL into a draft; verify the preset.
- `install_timeline.py` — append as timeline 02+, encrypt, read back.
- `rework_subtitles.py`, `subtitle_audit.py`, `remap_subtitles.py` — pass-2 subtitle chain.
- `build_fx_library.py`, `check_resources.py`, `restore_fx.py`, `restore_dlls.py` — asset
  library, resource validation, offline package recovery, DLL mirror.
- `audit_bounds.py` — read-only pre-delivery boundary audit. Run it and actually read the
  four lines it prints per clip: `START-MIDSENTENCE`, `END-CLIPPED`, `TITLE-LATE` /
  `TITLE-ANCHOR`. It is a tripwire, not a judge — expect occasional false positives and
  resolve them by reading. Also use it as a **count check**: the number of Step 1a requests
  found must equal the number of host-flagged clips delivered; if they differ, cut the missing
  one or say in the report why it was not delivered.

References (`references/`): `toolchain.md` (NLE structure, encryption, masks, install traps),
`preset.md` (fixed parameters and asset ids), `selection.md` (admission checks in detail),
`deletion-and-proofreading.md`, `subtitle-pipeline.md`, `effect-sfx.md`, `resource-cache.md`,
`batch-pipeline.md`.

Assets: `preset.json` (the enforced template), `edl.example.json`. `fx_library.json` is
generated locally by `build_fx_library.py` and is machine-specific, so it is gitignored.
`selection-profile.md` is personal config and is gitignored too.
