#!/usr/bin/env python3
"""Audit a JianYing draft's subtitles: coverage, gaps, repeats, boundaries.

Exit code 1 when a gate fails, so it can be used as a batch checkpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from check_resources import load_draft, resolve_draftc
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FILLERS = "呃啊唉哦嗯呀嘛哈哎"
FUNC = "的地得了着过和与或而但就也还又才都把被给对在从向为以于跟同吗呢吧啊呀嘛"
REPEAT_TOKENS = ["我", "你", "他", "她", "它", "这个", "那个", "就是", "然后", "所以", "但是",
                 "就", "很", "都", "也", "还", "又", "再", "对"]
AA_WHITELIST = ["看看", "试试", "说说", "谢谢", "慢慢", "刚刚", "常常", "天天", "好好", "多多",
                "早早", "远远", "深深", "轻轻", "静静", "悄悄", "渐渐", "纷纷", "一一", "拜拜",
                "了了", "哈哈", "嘿嘿", "呵呵", "嘻嘻"]


def subtitles_of(d: dict) -> list[dict]:
    idmap = {}
    for cat, arr in (d.get("materials") or {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idmap[m["id"]] = (cat, m)
    out = []
    for t in d.get("tracks") or []:
        for s in t.get("segments") or []:
            _, mat = idmap.get(s.get("material_id"), ("", {}))
            if mat.get("type") != "subtitle":
                continue
            try:
                text = json.loads(mat.get("content") or "{}").get("text", "")
            except json.JSONDecodeError:
                text = ""
            tr = s.get("target_timerange") or {}
            start = float(tr.get("start") or 0) / 1e6
            dur = float(tr.get("duration") or 0) / 1e6
            out.append({"start": round(start, 3), "end": round(start + dur, 3),
                        "duration": round(dur, 3), "text": text})
    out.sort(key=lambda x: x["start"])
    return out


def repeat_hits(text: str) -> list[str]:
    hits = []
    for tok in REPEAT_TOKENS:
        if tok + tok in text and tok + tok not in AA_WHITELIST:
            hits.append(tok + tok)
    for m in re.finditer(r"([^\d\s])\1{2,}", text):
        if m.group(0) not in AA_WHITELIST and m.group(1) not in "哈嘿呵嘻":
            hits.append(m.group(0))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True, help="draft_content.json or draft folder")
    ap.add_argument("--jy-draftc", default=os.environ.get("JY_DRAFTC", "jy-draftc"))
    ap.add_argument("--jy-dir", default=os.environ.get("JY_INSTALL_DIR"))
    ap.add_argument("--min-coverage", type=float, default=98.0)
    ap.add_argument("--max-small-gaps", type=int, default=30,
                    help="reference timeline has 29 small gaps; new edits should be fewer")
    ap.add_argument("--max-bad-ratio", type=float, default=40.0)
    ap.add_argument("--max-repeats", type=int, default=2,
                    help="budget; reference timeline has 2, new edits should aim for 0")
    args = ap.parse_args()

    args.jy_draftc = resolve_draftc(args.jy_draftc)
    if not (Path(args.jy_draftc).exists() or shutil.which(args.jy_draftc)):
        raise SystemExit(f"找不到 jy-draftc（{args.jy_draftc}）；加 --jy-draftc <exe 路径> "
                         f"或设环境变量 JY_DRAFTC，否则读不了剪映 11.x 的密文草稿")
    d = load_draft(Path(args.content), args.jy_draftc, args.jy_dir)
    subs = subtitles_of(d)
    duration = float(d.get("duration") or (subs[-1]["end"] if subs else 0)) / 1e6
    if not subs:
        raise SystemExit("no subtitles found")

    coverage = sum(s["duration"] for s in subs) / duration * 100 if duration else 0
    gaps = [round(b["start"] - a["end"], 3) for a, b in zip(subs, subs[1:])]
    small_gaps = [g for g in gaps if g > 0.005]
    long_gaps = [g for g in gaps if g > 0.3]
    lens = [len(s["text"]) for s in subs]
    repeats = [(s["start"], s["text"], repeat_hits(s["text"])) for s in subs if repeat_hits(s["text"])]
    at_hits = [(s["start"], s["text"]) for s in subs
               if re.search(r"(?<![A-Za-z])at(?![A-Za-z])", s["text"], re.I)]
    bad = []
    for a, b in zip(subs, subs[1:]):
        if a["text"] and b["text"] and (a["text"][-1] in FUNC or b["text"][0] in FUNC
                                        or len(a["text"]) <= 2 or len(b["text"]) <= 2):
            bad.append((a["start"], a["text"], b["text"]))
    bad_ratio = len(bad) / max(1, len(subs) - 1) * 100

    print(f"subtitles: {len(subs)}  duration: {duration:.1f}s")
    print(f"coverage : {coverage:.1f}%  (target >= {args.min_coverage}%)")
    print(f"gaps     : small(>0.005s)={len(small_gaps)} long(>0.3s)={len(long_gaps)} total={sum(small_gaps):.1f}s")
    print(f"length   : max={max(lens)} avg={sum(lens)/len(lens):.2f} over12={sum(1 for x in lens if x > 12)}")
    print(f"repeats  : {len(repeats)}  (budget <= {args.max_repeats})")
    for st, text, hits in repeats[:10]:
        print(f"   {st:7.2f} {text}  {hits}")
    print(f"at->@    : {len(at_hits)}")
    for st, text in at_hits[:5]:
        print(f"   {st:7.2f} {text}")
    print(f"weak boundary ratio: {bad_ratio:.1f}%  (target <= {args.max_bad_ratio}%)")
    for st, a, b in bad[:8]:
        print(f"   {st:7.2f} 「{a}」|「{b}」")

    failed = (coverage < args.min_coverage or len(small_gaps) > args.max_small_gaps
              or len(repeats) > args.max_repeats or len(at_hits) > 0 or bad_ratio > args.max_bad_ratio)
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())