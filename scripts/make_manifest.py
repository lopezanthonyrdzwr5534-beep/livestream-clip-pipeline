#!/usr/bin/env python3
"""Turn cue-index selections into the auto-editor manifest.

Selection is done by subtitle index (Step 1/1c), deletion by subtitle index
(Step 2b); this is the only place those indices become timestamps. Doing that
conversion by hand is how half-sentences end up split across two clips.

  clips_idx.tsv      name<TAB>first_cue<TAB>last_cue     (one line per topic)
  delete_idx.txt     one cue index per line              (optional, 整条级废句)

Out: manifest.tsv    HH:MM:SS.mmm<TAB>HH:MM:SS.mmm<TAB>name
                     same name repeated = several kept ranges, concatenated
                     into one finished clip by pipeline_mp4.py
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r", "")
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [x for x in block.split("\n") if x.strip()]
        if len(lines) < 2:
            continue
        m_idx = re.match(r"^\s*(\d+)\s*$", lines[0])
        m_ts = re.match(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})", lines[1])
        if not m_idx or not m_ts:
            continue
        cues.append({"i": int(m_idx.group(1)),
                     "s": to_sec(m_ts.group(1)), "e": to_sec(m_ts.group(2)),
                     "t": " ".join(lines[2:])})
    cues.sort(key=lambda c: c["i"])
    return cues


def to_sec(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def hms(t: float) -> str:
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True)
    ap.add_argument("--clips", required=True, help="name<TAB>first_cue<TAB>last_cue")
    ap.add_argument("--delete", default=None, help="cue indices, one per line")
    ap.add_argument("--out", required=True, help="manifest.tsv")
    ap.add_argument("--min-keep", type=float, default=0.25,
                    help="drop a kept sliver shorter than this (s)")
    ap.add_argument("--min-final", type=float, default=300.0,
                    help="成片下限（秒）。预估成片低于它直接失败——低于下限通常意味着"
                         "这段没讲完，而不是话题太短（2026-10-28 4:50 那条就是这么漏的）")
    ap.add_argument("--est-ratio", type=float, default=0.6,
                    help="预估成片 = 保留时长 × 这个系数（剪气口 -28%% 、1.2× -17%%）")
    ap.add_argument("--check-tail", type=int, default=5,
                    help="终点后强制摊出多少条字幕，用来判有没有收口")
    ap.add_argument("--allow-short", action="store_true", help="明知低于下限也放行")
    ap.add_argument("--allow-continuation", action="store_true",
                    help="终点后紧跟承接句也放行（等于人工确认话题确实断了）")
    args = ap.parse_args()

    cues = parse_srt(Path(args.srt))
    if not cues:
        raise SystemExit("SRT 解析出 0 条，检查编码/换行")
    by_idx = {c["i"]: c for c in cues}

    drop = set()
    if args.delete:
        for ln in Path(args.delete).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            for tok in re.split(r"[,\s]+", ln):
                if tok.isdigit():
                    drop.add(int(tok))
    unknown = sorted(x for x in drop if x not in by_idx)
    if unknown:
        raise SystemExit(f"要删的字幕序号不存在: {unknown[:10]}")

    rows, report = [], []
    seen = []
    for ln in Path(args.clips).read_text(encoding="utf-8-sig").splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = ln.split("\t")
        if len(f) < 3:
            raise SystemExit(f"clips 行格式错（要 name<TAB>起序号<TAB>止序号）: {ln!r}")
        name, a, b = f[0].strip(), int(f[1]), int(f[2])
        if name in seen:
            raise SystemExit(f"片名重复: {name}（同一条片子的多区间请写在同一行区间内，"
                             f"删句用 --delete）")
        seen.append(name)
        if a > b:
            a, b = b, a
        missing = [x for x in range(a, b + 1) if x not in by_idx]
        if missing:
            raise SystemExit(f"{name}: 序号 {missing[:5]} 不在 SRT 里")

        # complement of the deleted cues, as contiguous kept ranges.
        # Cues are never butted together (0.1-2s of silence between them), so the
        # gap must NOT split a range: only a deleted cue does. The silence inside a
        # range is exactly what auto-editor removes downstream — if we split on it we
        # get one range per cue (163 pieces for a 10-minute clip) and the concat
        # turns into a choppy mess.
        ranges, cur = [], None
        removed = 0.0
        for i in range(a, b + 1):
            c = by_idx[i]
            if i in drop:
                removed += c["e"] - c["s"]
                if cur:
                    ranges.append(cur)
                    cur = None
                continue
            if cur is None:
                cur = {"s": c["s"], "e": c["e"]}
            else:
                cur["e"] = max(cur["e"], c["e"])
        if cur:
            ranges.append(cur)

        ranges = [r for r in ranges if r["e"] - r["s"] >= args.min_keep]
        if not ranges:
            raise SystemExit(f"{name}: 删完没有保留段了，检查 --delete")
        span = by_idx[b]["e"] - by_idx[a]["s"]
        kept = sum(r["e"] - r["s"] for r in ranges)
        for r in ranges:
            rows.append((r["s"], r["e"], name))
        report.append((name, a, b, span, kept, removed, len(ranges)))

    Path(args.out).write_text(
        "\n".join(f"{hms(s)}\t{hms(e)}\t{n}" for s, e, n in rows) + "\n", encoding="utf-8")

    print(f"{'片名':<28}{'序号':>10}{'跨度':>9}{'保留':>9}{'删句':>8}{'段数':>5}")
    for name, a, b, span, kept, removed, nr in report:
        print(f"{name[:28]:<28}{a}-{b:>5}{span:>9.1f}{kept:>9.1f}{removed:>8.1f}{nr:>5}")
    print(f"\n清单 -> {args.out}  （{len(rows)} 行 / {len(report)} 条片子）")

    # ---- 两道闸门 --------------------------------------------------------------
    # 这两个判断以前靠"我想起来要读一下"，事实证明靠不住：2026-10-28 有一条成片
    # 4:50（下限 5:00）、终点后面还挂着同一段论证，两处都没被拦住。改成跑不过。
    CONT = ("我再强调", "就这两个", "这就是", "所以只有", "那就是", "所以说",
            "我再讲", "再往下", "还有一个")
    short, cont = [], []
    print("\n终点后 %d 条（判断有没有收口，必须逐条读）：" % args.check_tail)
    for name, a, b, span, kept, removed, nr in report:
        tail = [by_idx[i] for i in range(b + 1, b + 1 + args.check_tail) if i in by_idx]
        est = kept * args.est_ratio
        mark = ""
        if est < args.min_final:
            short.append((name, est))
            mark += " [SHORT]"
        joined = "".join(c["t"] for c in tail)
        hit = [k for k in CONT if k in joined]
        if hit:
            cont.append((name, hit[0]))
            mark += " [CONTINUATION]"
        print("  %s 止于「%s」%s" % (name[:20], by_idx[b]["t"][:16], mark))
        for c in tail:
            print("      %6.1f  %s" % (c["s"], c["t"]))

    fail = False
    if short and not args.allow_short:
        print("\n✗ 预估成片低于下限 %.0fs：" % args.min_final)
        for n, e in short:
            print("    %-28s 约 %.0fs —— 先怀疑「这段没讲完」，回头读终点后 5–10 条；"
                  "确认话题就这么长再加 --allow-short" % (n[:28], e))
        fail = True
    if cont and not args.allow_continuation:
        print("\n✗ 终点后紧跟承接句（话题没断）：")
        for n, k in cont:
            print("    %-28s 命中「%s」—— 默认并进同一话题；确认要断开再加 --allow-continuation"
                  % (n[:28], k))
        fail = True
    if fail:
        print("\n清单已写出，但未通过闸门：别直接拿去跑截段。")
        return 1
    print("\n闸门通过（时长、收口都过）。成片≈跨度×0.6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
