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

`clips.tsv` — one clip per line: `start<TAB>end<TAB>name`; timestamps in SRT form (`hh:mm:ss,ms` or `hh:mm:ss.ms`); `#` lines ignored.
Tuning: `--margin 0.4s` (cuts too choppy → raise), `--edit "audio:threshold=0.005"` (silence left → raise), `--keep-raw` (debug), `--dry-run`.

## Design notes (field-tested)

- Verify SRT ↔ video timeline alignment before cutting (SRT max timestamp ≈ video duration, same origin); misalignment cuts the wrong scene silently.
- A clip counts as trimmed only if `final < raw`; report honestly when a span holds no detectable silence.
- Flag >30% removal per clip for a spot-check so speech never gets chopped.
- For transcripts over ~2h, let a sub-task read the whole thing and return only the ranked table.

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

manifest 为 TSV，每行 `开始<TAB>结束<TAB>输出名`，时间码支持 `hh:mm:ss,ms` 或 `hh:mm:ss.ms`。常用参数：`--margin 0.4s`（防剪碎）、`--edit "audio:threshold=0.005"`（多剪）、`--keep-raw`（排查）、`--dry-run`（先验）。

**设计约束（实测沉淀）**：

- 开工前必须核验 SRT 与原片时间轴对齐（SRT 最大时间戳 ≈ 视频时长、同起点）；多段原片先确认 SRT 对应哪一段——错轴会安静地截错场景。
- 成片必须 `剪后 < 截出` 才算剪到东西；相等时如实报告"该段无可剪静音"，不得谎称已剪。
- 剪除比例超过 30% 的切片单独提示抽看，防止把话剪碎。
- 超长转录（>2 小时）的通读交给子任务，主线只接收排序后的选段表。

## License

[MIT](LICENSE) — © 2026 lopezanthonyrdzwr5534-beep
