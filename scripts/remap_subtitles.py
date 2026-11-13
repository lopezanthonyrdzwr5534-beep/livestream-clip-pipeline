#!/usr/bin/env python3
"""Remap subtitles from an old cut to a new cut using source-time anchors.

Subtitles are stored in absolute timeline time. After inserting/removing video
segments, every downstream subtitle shifts by a different amount; a global
"select all + shift" cannot fix that. This tool maps each old subtitle back to
source time through the old video track, then forward to the new target time
through the new video track.
"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def uid() -> str:
    return str(uuid.uuid4()).upper()


def video_segments(d: dict) -> list[dict]:
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
            if mat.get("type") != "video":
                continue
            tr = s.get("target_timerange") or {}
            sr = s.get("source_timerange") or {}
            out.append({
                "start": float(tr.get("start") or 0) / 1e6,
                "end": float((tr.get("start") or 0) + (tr.get("duration") or 0)) / 1e6,
                "source_start": float(sr.get("start") or 0) / 1e6,
                "source_end": float((sr.get("start") or 0) + (sr.get("duration") or 0)) / 1e6,
                "speed": float(s.get("speed") or 1.0),
            })
    return sorted(out, key=lambda x: x["start"])


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
            tr = s.get("target_timerange") or {}
            out.append({
                "start": float(tr.get("start") or 0) / 1e6,
                "end": float((tr.get("start") or 0) + (tr.get("duration") or 0)) / 1e6,
                "text": json.loads(mat.get("content") or "{}").get("text", ""),
            })
    return sorted(out, key=lambda x: x["start"])


def target_to_source(segs: list[dict], t: float) -> float | None:
    for s in segs:
        if s["start"] - 1e-6 <= t < s["end"] + 1e-6:
            return s["source_start"] + (t - s["start"]) * s["speed"]
    return None


def source_to_target(segs: list[dict], t: float) -> float | None:
    for s in segs:
        if s["source_start"] - 1e-6 <= t < s["source_end"] + 1e-6:
            return s["start"] + (t - s["source_start"]) / s["speed"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="old plaintext draft with subtitles + old video cuts")
    ap.add_argument("--new", required=True, help="new plaintext draft with new video cuts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    old = load(args.old)
    new = load(args.new)
    old_segs = video_segments(old)
    new_segs = video_segments(new)
    old_subs = subtitles_of(old)
    mapped = []
    dropped = partial = 0
    for x in old_subs:
        a = target_to_source(old_segs, x["start"])
        b = target_to_source(old_segs, x["end"])
        if a is None or b is None:
            dropped += 1
            continue
        na = source_to_target(new_segs, a)
        nb = source_to_target(new_segs, b)
        if na is None and nb is None:
            dropped += 1
            continue
        if na is None or nb is None:
            partial += 1
        if na is None:
            na = max(0.0, (nb or 0) - 0.3)
        if nb is None:
            nb = na + 0.3
        if nb <= na + 0.05:
            dropped += 1
            continue
        mapped.append({"start": round(na, 3), "end": round(nb, 3), "text": x["text"]})

    # style template from old draft
    tidmap = {}
    for cat, arr in old.get("materials", {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    tidmap[m["id"]] = (cat, m)
    sub_track = next(t for t in old["tracks"] if t.get("segments") and
                     tidmap.get(t["segments"][0]["material_id"], ("", {}))[1].get("type") == "subtitle")
    sub_seg = sub_track["segments"][0]
    sub_mat = tidmap[sub_seg["material_id"]][1]

    # remove old subtitle tracks/materials from new
    idmap = {}
    for cat, arr in new.get("materials", {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idmap[m["id"]] = (cat, m)
    keep = []
    for t in new.get("tracks", []):
        if t.get("segments"):
            _, mat = idmap.get(t["segments"][0].get("material_id"), ("", {}))
            if mat.get("type") == "subtitle":
                continue
        keep.append(t)

    new_segments, new_ids = [], set()
    for p in mapped:
        mat = copy.deepcopy(sub_mat)
        mat["id"] = uid()
        try:
            obj = json.loads(mat.get("content") or "{}")
        except json.JSONDecodeError:
            obj = {}
        obj["text"] = p["text"]
        if obj.get("styles"):
            obj["styles"][0]["range"] = [0, len(p["text"])]
        mat["content"] = json.dumps(obj, ensure_ascii=False)
        mat["recognize_text"] = p["text"]
        new["materials"].setdefault("texts", []).append(mat)
        new_ids.add(mat["id"])
        seg = copy.deepcopy(sub_seg)
        seg["id"] = uid()
        seg["material_id"] = mat["id"]
        seg["target_timerange"] = {"start": int(round(p["start"] * 1e6)),
                                   "duration": int(round((p["end"] - p["start"]) * 1e6))}
        seg["extra_material_refs"] = []
        seg["render_index"] = 15000
        seg["track_render_index"] = 8
        new_segments.append(seg)
    track = copy.deepcopy(sub_track)
    track["id"] = uid()
    track["segments"] = new_segments
    keep.append(track)
    new["tracks"] = keep
    new["materials"]["texts"] = [m for m in new["materials"].get("texts", [])
                                 if m.get("type") != "subtitle" or m.get("id") in new_ids]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    report = {"old_subtitles": len(old_subs), "mapped": len(mapped),
              "partial": partial, "dropped": dropped}
    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
