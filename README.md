# 直播切片流水线 · Livestream Clip Pipeline

一个 Qoder Skill：把**直播/播客录屏 + SRT 字幕**一步做成**剪好气口的成品切片**。

三步流水线：**选段 → 截段 → 剪气口**

1. **选段** — 首次使用访谈你的选段标准（什么是好切片、不要什么、时长/条数），固化到
   `selection-profile.md`，之后每次都按你的标准语义挑选、排序、起标题。
2. **截段** — FFmpeg 按 SRT 时间戳精确截取（输入定位+重编码，帧精确，自动兼容
   HEVC/H.264 源）。
3. **剪气口** — auto-editor 自动去除每段的静音/停顿，逐条回报"跨度/截出/剪后"时长。

## 依赖

- Python 3
- [auto-editor](https://github.com/WyattBlue/auto-editor)：`python -m pip install auto-editor`
- [FFmpeg](https://github.com/FFmpeg/FFmpeg)：Windows 推荐 `winget install Gyan.FFmpeg`

`scripts/pipeline.py` 会自动在 PATH / pip --user Scripts / winget 包目录中定位二者。

## 安装

把本目录整个复制到 Qoder 技能目录即可：

- Windows: `C:\Users\<you>\.qoder\skills\livestream-clip-pipeline\`
- 重启会话或 `/skills reload`，用 `/livestream-clip-pipeline` 触发，
  或直接对 AI 说："用直播切片流水线处理这份 SRT 和原片"。

## 使用

```text
你: 跑直播切片流水线  <SRT 路径>  <原片路径>
AI: （首次）问 3 个标准问题 → 写 selection-profile.md → 通读字幕选段
    → 生成 manifest → dry-run → FFmpeg 截段 + auto-editor 剪气口
    → 交付表格：文件 | 星级 | 跨度 | 截出 | 剪后 | 气口
```

随时可改标准："把每次条数改成 3–5 条" / "这次输出放 X 目录"。

也可以脱离 AI 单独用脚本（手工写好 manifest 后）：

```bash
python scripts/pipeline.py --src REC.mp4 --manifest clips.tsv --outdir OUT/ --dry-run
```

manifest 为 TSV：`开始<TAB>结束<TAB>输出名`，时间为 SRT 时间码（`hh:mm:ss,ms` 或 `hh:mm:ss.ms`）。
常用参数：`--margin 0.4s`（防剪碎）、`--edit "audio:threshold=0.005"`（多剪）、
`--keep-raw`（保留中间件排查）。

## 设计约束（实测沉淀）

- 开工前必须核验 SRT 与原片时间轴对齐（最大时间戳 ≈ 视频时长、同起点）；
  直播被拆成多段文件时，先确认 SRT 对应哪一段。
- 成片一律 `final < raw` 才算剪到东西；若相等要如实报告"该段无静音"，不谎称已剪。
- 剪除比例 >30% 的切片提示抽看，防止把话剪碎。
- 长转录（>2h）的通读交给子代理，主线只接收排序后的选段表。

## 注意

`selection-profile.md` 是个人配置（你的选段标准），由首次访谈生成，已通过
`.gitignore` 排除，不会进版本库。
