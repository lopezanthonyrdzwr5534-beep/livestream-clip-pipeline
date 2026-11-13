#!/usr/bin/env python3
"""Master-timeline (1.2x) clip selection helper.

All times here are **master final-timeline seconds** (the 1.2x replay / its SRT).
The output clip length equals the span, so no speed arithmetic happens here.

subcommands
  dump     SRT -> TSV (idx, start, end, text) for audit_bounds.py --dump
  blocks   candidate topic blocks split by long pauses (>= --gap), with durations
  marks    lines where the host names the editor ("帮我剪/打个标记"), plus context
  dirty    crude-word hits inside a cue range
  show     print cues in an index/time window (first/last 3 for boundary checks)
  anchor   verify every title anchor is literally present and sits in the front part
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DIRTY = r"他妈|他娘|傻逼|傻B|我操|卧槽|滚|骗子|割韭菜|垃圾|脑残|神经病|去死"
MARK = r"帮我剪|剪出来|剪下来|剪成切片|打个标|打个标记|这段.{0,6}剪辑|单独.{0,4}剪|剪辑.{0,4}剪"
MARK_NOISE = r"标准|目标|评判|剪指甲|做剪辑的|给作业|打分|进行标记"


def to_s(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = [float(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def to_ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def load_srt(path: str) -> list[dict]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        text = " ".join(lines[2:]).strip().replace("<br>", " ").replace("<br/>", " ")
        if text:
            cues.append({"start": to_s(a), "end": to_s(b), "text": text})
    cues.sort(key=lambda c: c["start"])
    for i, c in enumerate(cues, 1):
        c["idx"] = i
    return cues


def load_dump(path: str) -> list[dict]:
    cues = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        f = line.split("\t")
        if len(f) < 4:
            continue
        try:
            st, en = to_s(f[1]), to_s(f[2])
        except ValueError:
            continue
        cues.append({"idx": int(f[0]), "start": st, "end": en, "text": f[3]})
    return cues


def read_cues(args) -> list[dict]:
    if getattr(args, "dump", None):
        return load_dump(args.dump)
    if getattr(args, "srt", None):
        return load_srt(args.srt)
    if getattr(args, "draft", None):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from rework_subtitles import subtitles_of  # reuse the reader

        d = json.loads(Path(args.draft).read_text(encoding="utf-8"))
        cues = []
        for i, s in enumerate(subtitles_of(d), 1):
            cues.append({"idx": i, "start": round(s["start"], 3),
                         "end": round(s["end"], 3), "text": s["text"]})
        return cues
    raise SystemExit("need --srt / --dump / --draft")


def win(cues: list[dict], a: float | None, b: float | None,
        i0: int | None, i1: int | None) -> list[dict]:
    if i0 is not None or i1 is not None:
        lo, hi = i0 or 1, i1 or len(cues)
        return [c for c in cues if lo <= c["idx"] <= hi]
    if a is not None or b is not None:
        lo, hi = a if a is not None else float("-inf"), b if b is not None else float("inf")
        return [c for c in cues if c["start"] < hi and c["end"] > lo]
    return cues


def cmd_dump(args) -> int:
    cues = read_cues(args)
    out = Path(args.out) if args.out else Path(args.srt or args.draft).with_suffix(".tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{c['idx']}\t{to_ts(c['start'])}\t{to_ts(c['end'])}\t{c['text']}\n"
                          for c in cues), encoding="utf-8")
    print(f"dumped {len(cues)} cues -> {out}")
    return 0


def cmd_blocks(args) -> int:
    cues = win(read_cues(args), args.from_, args.to, None, None)
    if not cues:
        print("no cues in window")
        return 1
    blocks, cur = [], [cues[0]]
    for prev, c in zip(cues, cues[1:]):
        if c["start"] - prev["end"] >= args.gap:
            blocks.append(cur)
            cur = [c]
        else:
            cur.append(c)
    blocks.append(cur)
    rows = []
    for blk in blocks:
        dur = blk[-1]["end"] - blk[0]["start"]
        if dur < args.min_dur:
            continue
        rows.append((blk[0]["start"], blk[-1]["end"], dur, blk[0]["idx"], blk[-1]["idx"],
                     blk[0]["text"], blk[-1]["text"]))
    rows.sort(key=lambda r: -r[2])
    print(f"gap>={args.gap}s  blocks>= {args.min_dur}s: {len(rows)}")
    for st, en, dur, i0, i1, first, last in rows:
        flag = "OK " if dur >= args.target else "短 "
        print(f"{flag}{to_ts(st)}->{to_ts(en)} {dur/60:5.1f}min cues {i0}-{i1} | {first[:22]} … {last[:22]}")
    return 0


def cmd_marks(args) -> int:
    cues = read_cues(args)
    hits = [c for c in cues if re.search(args.pattern, c["text"]) and not re.search(MARK_NOISE, c["text"])]
    print(f"点名/标记命中 {len(hits)} 处（一场通常 2-3 处，逐条都要交付）")
    for c in hits:
        print(f"\n[{c['idx']}] {to_ts(c['start'])}  {c['text']}")
        for n in [x for x in cues if c["idx"] - 3 <= x["idx"] <= c["idx"] + 3 and x is not c]:
            print(f"   ({'+' if n['idx'] > c['idx'] else '-'}) {n['idx']} {to_ts(n['start'])} {n['text'][:34]}")
    return 0


def cmd_dirty(args) -> int:
    cues = win(read_cues(args), args.from_, args.to, args.idx_from, args.idx_to)
    hits = [c for c in cues if re.search(args.pattern, c["text"], re.I)]
    print(f"脏话/敏感词命中 {len(hits)} 条（整条挖掉或弃用该段，逐条决定）")
    for c in hits:
        print(f"[{c['idx']}] {to_ts(c['start'])}-{to_ts(c['end'])}  {c['text']}")
    return 0


def cmd_show(args) -> int:
    cues = read_cues(args)
    sel = win(cues, args.from_, args.to, args.idx_from, args.idx_to)
    if args.edge and sel:
        idx = {c["idx"] for c in sel}
        lo, hi = min(idx), max(idx)
        sel = [c for c in cues if lo - args.edge <= c["idx"] <= hi + args.edge]
        marks = {lo: "START", hi: "END"}
    for c in sel:
        tag = marks.get(c["idx"], "") if args.edge else ""
        print(f"[{c['idx']:5}] {to_ts(c['start'])} {to_ts(c['end'])}  {c['text']}  {tag}")
    return 0


def cmd_anchor(args) -> int:
    cues = read_cues(args)
    if not cues:
        print("no cues")
        return 1
    lo = win(cues, args.from_, args.to, args.idx_from, args.idx_to)
    total = max(c["end"] for c in lo) - min(c["start"] for c in lo)
    print(f"标题：{args.title}")
    ok = True
    for token in re.split(r"[\s，,。.、/]+", args.title):
        token = token.strip("「」\"'（）()")
        if len(token) < 2:
            continue
        hits = [c for c in lo if token in c["text"]]
        if not hits:
            print(f"  ✗ {token}：区间内搜不到（别把没说的话写进标题）")
            ok = False
            continue
        pos = (hits[0]["start"] - min(c["start"] for c in lo)) / total if total else 0
        flag = "✓" if pos <= args.max_pos else "△"
        if pos > args.max_pos:
            ok = False
        print(f"  {flag} {token}：首现 {to_ts(hits[0]['start'])}（区间 {pos*100:.0f}% 处）"
              f"{'  ← 偏后，起点选错了' if flag == '△' else ''}")
    print("结论：" + ("可下刀" if ok else "需改标题或重选起点"))
    return 0


def add_window(p, edge=False, idx=True):
    p.add_argument("--srt", help="SRT in master (1.2x) time")
    p.add_argument("--dump", help="cue TSV produced by `dump`")
    p.add_argument("--draft", help="decrypted master draft_content.json (subtitle track)")
    p.add_argument("--from", dest="from_", type=float, default=None)
    p.add_argument("--to", type=float, default=None)
    if idx:
        p.add_argument("--idx-from", type=int, default=None)
        p.add_argument("--idx-to", type=int, default=None)
    if edge:
        p.add_argument("--edge", type=int, default=3, help="print N cues outside the boundary too")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dump", help="SRT/draft -> cue TSV")
    add_window(p, idx=False)
    p.add_argument("--out")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("blocks", help="pause-split candidate topic blocks")
    add_window(p)
    p.add_argument("--gap", type=float, default=4.0, help="pause length that ends a block (s)")
    p.add_argument("--min-dur", type=float, default=180.0, help="drop blocks shorter than this (s)")
    p.add_argument("--target", type=float, default=300.0, help=">= this counts as a full-length clip (s)")
    p.set_defaults(func=cmd_blocks)

    p = sub.add_parser("marks", help="host-named clip requests (Step 1a)")
    add_window(p, idx=False)
    p.add_argument("--pattern", default=MARK)
    p.set_defaults(func=cmd_marks)

    p = sub.add_parser("dirty", help="crude-word hits in a window")
    add_window(p)
    p.add_argument("--pattern", default=DIRTY)
    p.set_defaults(func=cmd_dirty)

    p = sub.add_parser("show", help="print cues, boundary-checked")
    add_window(p, edge=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("anchor", help="title anchor greppability + position")
    add_window(p)
    p.add_argument("--title", required=True)
    p.add_argument("--max-pos", type=float, default=0.45)
    p.set_defaults(func=cmd_anchor)

    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
