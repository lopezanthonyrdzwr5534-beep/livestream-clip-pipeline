# Livestream Clip Pipeline · 直播切片流水线

**English** · [中文](#中文版)

An **Agent Skill** (plus a standalone script) that turns a livestream or podcast recording and its SRT subtitles into finished short-video clips in one pass:

1. **Select** — read the transcript and pick high-value continuous segments. Your selection criteria are captured once via a 3-question interview, persisted to `selection-profile.md`, and reused on every later run (revise anytime).
2. **Cut** — FFmpeg extracts each segment by its SRT timestamps: frame-accurate input-seek + re-encode, HEVC/H.264 sources both fine.
3. **Trim** — auto-editor removes breaths and silence from each cut, reporting span / raw / final durations per clip.

## Requirements

- Python 3
- [auto-editor](https://github.com/WyattBlue/auto-editor) — `python -m pip install auto-editor`
- [FFmpeg](https://github.com/FFmpeg/FFmpeg) — `winget install Gyan.FFmpeg` (Windows) / `brew install ffmpeg` (macOS) / your package manager

The script auto-locates both tools (PATH, pip user Scripts, winget package dirs); override with `--ae`.

## Install as an agent skill

Copy this folder into the skills directory of any assistant that supports SKILL.md-style skills (e.g. `~/.claude/skills/`). Then say: *"run the livestream clip pipeline on this SRT and this recording."* First use asks you 3 questions and writes your profile; later runs apply it directly.

## Standalone script usage

Works without any AI assistant, once you have a manifest:

```bash
python scripts/pipeline.py --src REC.mp4 --manifest clips.tsv --outdir OUT/ --dry-run
```

`clips.tsv` — one range per line: `start<TAB>end<TAB>name`; timestamps in SRT form (`hh:mm:ss,ms` or `hh:mm:ss.ms`); `#` lines ignored. Lines sharing the same `name` are cut and concatenated into one clip (multi-range keep-cut for refined editing).
Tuning: `--margin 0.4s` (cuts too choppy → raise), `--edit "audio:threshold=0.005"` (silence left → raise), `--fps 30` (keep the default; only raise it if you truly need native frame rate and will check for black frames), `--keep-raw` (debug), `--dry-run`.

## Design notes (field-tested)

- Verify SRT ↔ video timeline alignment before cutting (SRT max timestamp ≈ video duration, same origin); misalignment cuts the wrong scene silently.
- A clip counts as trimmed only if `final < raw`; report honestly when a span holds no detectable silence.
- Flag >30% removal per clip for a spot-check so speech never gets chopped.
- For transcripts over ~2h, let a sub-task read the whole thing and return only the ranked table.
- **Budget duration as `final ≈ span × 0.7`.** Measured across three streams the real ratio swings
  between 0.69 and 0.84 (phone-in dialogue barely shrinks; monologue that keeps reading chat messages
  shrinks a lot), so 0.7 is the conservative planning number.
- **Silence is not a topic boundary.** Gaps >4s narrow candidate ranges only; the cut must land on a
  topic-turn line. Cutting on a pause has split one sentence across two clips.
- **Seven admission checks per candidate** before it enters the manifest: the opening cue is the topic's
  first line; opening cue, title and summary describe the same thing; the closing cue is a complete
  punchline; the span fits the duration band after the 0.7 budget; the title carries an anchor that is
  greppable in the subtitles and lands in the first 45% of the clip; no overlap with selected or
  host-flagged segments; profanity inside the span located and handled.
- **Segments the host asks for on air are delivered unconditionally** ("editor, cut this part out for
  me"). Enumerate every such line first — a stream usually has two or three — and remember the request
  line is rarely where the segment starts.
- Sub-agents reading long transcripts should return **cue indices only**; recompute timestamps and spans
  yourself, and re-read every "this is a digression, drop it" suggestion against the source.
- `scripts/audit_bounds.py` (read-only) prints each clip's two preceding / opening / closing / two
  following subtitle lines and flags `START-MIDSENTENCE`, `END-CLIPPED`, `TITLE-LATE`. Run it before
  delivery. It exists because formal checks (half sentences, timecodes, black frames) kept passing while
  the selection itself was wrong.
- **Black frames** (each one cost a re-render before the script handled it): cuts are normalized to `--fps 30` because auto-editor v29 inserts black frames at its cut points on 60fps sources; a preroll seek absorbs the black head that long-GOP HEVC/TS recordings show after a cold seek; and every finished clip goes through a `blackdetect=d=0.02` pass that trims away black lying within 1s of either edge.

---

## 中文版

**直播切片流水线**：一个 Agent Skill（附可独立运行的脚本），把「直播/播客录屏 + SRT 字幕」一步做成**剪好气口的成品切片**。

1. **选段** — 通读字幕，按你固化的标准挑高价值连续片段。首次使用只需回答 3 个问题（内容领域 / 好切片标准与明确不要什么 / 单片时长与条数），标准写入 `selection-profile.md`，之后每次都直接沿用，随时可改。
2. **截段** — FFmpeg 按 SRT 时间码帧精确截取（输入定位 + 重编码；自动兼容 HEVC/H.264 源）。
3. **剪气口** — auto-editor 逐条去除停顿与静音，并回报每条「跨度 / 截出 / 剪后」时长对照。

**依赖**：Python 3；`python -m pip install auto-editor`；FFmpeg（Windows 推荐 `winget install Gyan.FFmpeg`）。脚本会自动在 PATH、pip 用户目录、winget 包目录中定位工具。

**安装**：把本目录整体拷入你所用 AI 助手的技能目录（任何支持 SKILL.md 格式的助手，如 `~/.claude/skills/`），对它说"用直播切片流水线处理这份 SRT 和原片"即可；首次会访谈，之后直接按画像执行。

**脱离助手单独用脚本**（手工写好 manifest 后）：

```bash
python scripts/pipeline.py --src REC.mp4 --manifest clips.tsv --outdir OUT/
```

manifest 为 TSV，每行 `开始<TAB>结束<TAB>输出名`，时间码支持 `hh:mm:ss,ms` 或 `hh:mm:ss.ms`；**同名的多行会各自截取再拼接为一条成片**（精剪保留段用法）。常用参数：`--margin 0.4s`（防剪碎）、`--edit "audio:threshold=0.005"`（多剪）、`--fps 30`（保持默认即可；确需原帧率再改，并自行检查黑帧）、`--keep-raw`（排查）、`--dry-run`（先验）。

**设计约束（实测沉淀）**：

- 开工前必须核验 SRT 与原片时间轴对齐（SRT 最大时间戳 ≈ 视频时长、同起点）；多段原片先确认 SRT 对应哪一段——错轴会安静地截错场景。
- 成片必须 `剪后 < 截出` 才算剪到东西；相等时如实报告"该段无可剪静音"，不得谎称已剪。
- 剪除比例超过 30% 的切片单独提示抽看，防止把话剪碎。
- 超长转录（>2 小时）的通读交给子任务，主线只接收排序后的选段表。
- **黑帧**（每一条都先踩过坑才写进脚本）：截段统一归一化为 `--fps 30`，因为 auto-editor v29 在 60fps 源上会在自己的剪切点插入黑帧；长 GOP 的 HEVC/TS 源冷启动定位会出黑头，脚本用预滚（preroll）seek 规避；成片还会跑一遍 `blackdetect=d=0.02`，把落在首尾 1s 内的黑帧就地复剪掉。
- **时长按「成片 ≈ 跨度 × 0.7」预算。** 三场实测比值在 0.69–0.84 之间浮动（连麦对话几乎不缩，边念公屏提问边讲的独白段掉得多），所以规划一律按 0.7 保守估——宁可选长一点，也不要剪完才发现不够长。
- **停顿不等于话题转折。** 相邻字幕停顿 >4 秒只用来圈定候选范围，真正下刀必须落在话题转折句上；按停顿切过，出现过一整句话的前半在 A 片、后半在 B 片。
- **候选进清单前过七项准入检查**：起点句是本话题第一句（不是上一个提问的承接半句）；起点句·标题·内容概括三者说的是同一件事；终点句是完整收口（不能停在"嗯"这种单字条或半句上）；按 ×0.7 折算后落在时长档内；标题含一个能在字幕里逐字搜到的锚点、且出现在全片前 45%；不与已选段和点名段重叠；段内脏话敏感词已定位并决定删或弃。
- **主播在直播里点名要剪的段落一律交付。** 他常中途喊"这段那个剪辑帮我剪出来啊/打个标记"；先把全场命中行列全（一场通常有两三处），再往前找该话题真正的第一句——点名句本身几乎不是起点。
- 子任务通读长转录时**只要字幕序号**：时间码、跨度、时长全部主线自己回查计算（实测它的算术会错）；它标的"可挖掉的岔口"必须回读原文再决定，出现过它把整段最有戏的一句当成岔口建议删掉。
- `scripts/audit_bounds.py`（只读）把每条的「前 2 句 / 起点句 / 终点句 / 后 2 句」摊出来，并标 `START-MIDSENTENCE`／`END-CLIPPED`／`TITLE-LATE`。交付前必跑——它存在的原因就是：形式检查（半句、时间码、黑帧）全过了，选段照样是错的。

## License

[MIT](LICENSE) — © 2026 lopezanthonyrdzwr5534-beep
