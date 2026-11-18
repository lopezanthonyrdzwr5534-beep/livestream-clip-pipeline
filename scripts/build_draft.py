#!/usr/bin/env python3
"""Build a fixed-template JianYing draft from an EDL.

The template is a decrypted reference timeline (draft_content.json). The EDL
defines source keep-segments, subtitles (source time), titles and semantic
effect/SFX events. The output is a new draft folder with plaintext content.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import jieba
except ImportError:  # optional
    jieba = None

MAX_CHARS = 12  # merged-rule default: 12 chars per line


def uid() -> str:
    return str(uuid.uuid4()).upper()


def load_text(mat: dict) -> dict:
    try:
        return json.loads(mat.get("content") or "{}")
    except json.JSONDecodeError:
        return {}


def set_sub_text(mat: dict, text: str) -> None:
    obj = load_text(mat)
    obj["text"] = text
    if obj.get("styles"):
        obj["styles"][0]["range"] = [0, len(text)]
        obj["styles"] = obj["styles"][:1]
    mat["content"] = json.dumps(obj, ensure_ascii=False)
    mat["recognize_text"] = text
    mat["words"] = {}
    mat["current_words"] = {}
    mat["line_max_width"] = 1.0
    mat["force_apply_line_max_width"] = True


def split_text(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]

    def fits(cut: int) -> bool:
        return 0 < cut < len(text) and cut <= MAX_CHARS and len(text) - cut <= MAX_CHARS

    punct = "，,。！？；：、 "
    pc = []
    for i, ch in enumerate(text):
        if ch not in punct or not fits(i + 1):
            continue
        if ch in ",." and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
            continue
        pc.append(i + 1)
    if pc:
        cut = min(pc, key=lambda p: abs(p - len(text) / 2))
        return [text[:cut], text[cut:]]

    if jieba is not None:
        words = list(jieba.cut(text))
        end_len, start_len = {}, {}
        pos = 0
        for i, word in enumerate(words):
            pos += len(word)
            end_len[pos] = len(word)
            if i + 1 < len(words):
                start_len[pos] = len(words[i + 1])
        bounds = []
        for p in end_len:
            if not fits(p):
                continue
            a, b = text[p - 1], text[p]
            if (a.isascii() and (a.isalnum() or a in ".,:%")) or (b.isascii() and (b.isalnum() or b in ".,:%")):
                continue
            bounds.append(p)
        if bounds:
            func = set("的地得了着过和与或而但就也还又才都把被给对在从向为以于跟同吗呢吧啊呀嘛")
            def penalty(p: int) -> int:
                s = 0
                if text[p - 1] in func: s += 2
                if text[p] in func: s += 2
                if text[p] == "的": s += 3
                if end_len.get(p, 0) == 1: s += 2
                if start_len.get(p, 0) == 1: s += 1
                return s
            near = [p for p in bounds if abs(p - len(text) / 2) <= 2]
            if not near:
                near = [p for p in bounds if abs(p - len(text) / 2) <= 3]
            cut = min(near or bounds, key=lambda p: (penalty(p), abs(p - len(text) / 2)))
            return [text[:cut], text[cut:]]

    cut = len(text) // 2
    while cut < len(text) - 1 and text[cut - 1].isascii() and text[cut - 1].isalnum() and text[cut].isascii() and text[cut].isalnum():
        cut += 1
    return [text[:cut], text[cut:]]


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=width,height",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}
    dur = float(data.get("format", {}).get("duration") or 0)
    w = h = 0
    for s in data.get("streams", []):
        if s.get("width"):
            w, h = s["width"], s["height"]
            break
    return {"duration": dur, "width": w, "height": h}


def apply_subtitle_style(mat: dict, seg: dict, preset: dict, font_path: str) -> None:
    """Force the fixed subtitle look, whatever style the template timeline happened to carry.

    Without this the built draft silently inherits the reference timeline's own
    subtitle font/size (e.g. JianYing default zh-hans 8pt), which is exactly how a
    draft ends up 'not matching the template' while every JSON check still passes.
    """
    sub = preset.get("subtitle") or {}
    size = float(sub.get("size", 12))
    color = sub.get("color", "#FFFFFF")
    y = float(sub.get("y", -0.236))
    rgb = [round(int(color[i:i + 2], 16) / 255, 4) for i in (1, 3, 5)]
    for key in ("content", "base_content"):
        obj = load_text(mat) if key == "content" else json.loads(mat.get(key) or "{}")
        if not obj:
            continue
        styles = obj.get("styles") or [{}]
        st = styles[0]
        st["size"] = size
        st["font"] = {"id": "", "path": font_path}
        st.setdefault("fill", {})["content"] = {"render_type": "solid",
                                                "solid": {"alpha": 1.0, "color": rgb}}
        obj["styles"] = styles[:1]
        mat[key] = json.dumps(obj, ensure_ascii=False)
    mat["font_path"] = font_path
    mat["font_size"] = size
    mat["text_color"] = color
    mat["shadow_alpha"] = float(sub.get("shadow_alpha", 0.9))
    mat["line_max_width"] = float(sub.get("line_max_width", 1.0))
    mat["force_apply_line_max_width"] = True
    tr = seg.setdefault("clip", {}).setdefault("transform", {})
    tr["y"] = y


def find_font(name: str, roots: list[Path]) -> str | None:
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(name):
            if p.is_file():
                return p.as_posix()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, help="decrypted reference timeline folder")
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta-template", default=None, help="plaintext draft_meta_info.json template")
    ap.add_argument("--preset", default=str(Path(__file__).resolve().parent.parent / "assets" / "preset.json"))
    ap.add_argument("--font-cache", default=str(Path.home() / "AppData/Local/JianyingPro/User Data/Cache"),
                    help="where to look up the fixed subtitle font by filename")
    ap.add_argument("--fx-library", default=str(Path(__file__).resolve().parent.parent / "assets" / "fx_library.json"),
                    help="sounds/effects cloned from the creator's own timelines (build_fx_library.py)")
    ap.add_argument("--no-style-fix", action="store_true", help="keep the template timeline's own subtitle style")
    ap.add_argument("--ignore-kind-mismatch", action="store_true",
                    help="build anyway when an event's kind disagrees with the fx library")
    args = ap.parse_args()

    preset = json.loads(Path(args.preset).read_text(encoding="utf-8")) if Path(args.preset).exists() else {}
    fx_lib = json.loads(Path(args.fx_library).read_text(encoding="utf-8")) if Path(args.fx_library).exists() else {}
    if not fx_lib:
        print(f"WARN: no fx library at {args.fx_library}; only the template's own sounds/effects can be placed")

    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))

    # ---- pre-flight on event kinds -------------------------------------------------
    # An event whose kind disagrees with the fx library used to vanish behind a
    # "not in fx library" WARN, which reads like a missing package rather than a typo
    # in manual/*.json. The draft still builds and reports integrity, so the hole is
    # invisible until the export. Fail here, before anything is written.
    lib_assets_pre = (fx_lib.get("assets") or {}) if isinstance(fx_lib, dict) else {}
    if lib_assets_pre and not args.ignore_kind_mismatch:
        bad = []
        for e in edl.get("events") or []:
            ent = lib_assets_pre.get(e.get("name"))
            if ent and e.get("kind") and ent.get("kind") != e["kind"]:
                bad.append((e.get("name"), e["kind"], ent.get("kind")))
        if bad:
            for name, got, want in bad:
                print(f"ERROR: 事件「{name}」声明 kind={got}，但 fx_library 里它是 kind={want}；"
                      f"按 {got} 找会在模板和库里都落空，然后被静默跳过")
            raise SystemExit(
                f"event kind mismatch: {len(bad)} 个事件放不进去。"
                f"改 manual/<片名>.json 里的 kind 再重跑 plan_from_master.py，"
                f"或加 --ignore-kind-mismatch 明知故犯。")
    source = Path(edl["source"])
    speed = float(edl.get("speed", 1.2))
    total_src = float(edl.get("source_duration") or probe(str(source)).get("duration") or 0)
    if total_src <= 0:
        raise SystemExit("cannot determine source duration; add source_duration to EDL")
    keep = [{"src0": float(x["source_start"]), "src1": float(x["source_end"])} for x in edl["keep_segments"]]
    keep = [x for x in keep if x["src1"] > x["src0"]]
    if not keep:
        raise SystemExit("no keep_segments")
    segs, out = [], 0.0
    for k in keep:
        dur = (k["src1"] - k["src0"]) / speed
        segs.append({**k, "out0": out, "out1": out + dur})
        out += dur
    total = out

    def map_time(t: float) -> float | None:
        # boundary-inclusive: a subtitle that ends exactly on a cut must map to
        # the segment end, otherwise the 0-gap chain breaks and coverage drops
        for s in segs:
            if s["src0"] - 1e-6 <= t <= s["src1"] + 1e-6:
                return s["out0"] + min(max(t - s["src0"], 0.0), s["src1"] - s["src0"]) / speed
        return None

    template = json.loads((Path(args.template) / "draft_content.json").read_text(encoding="utf-8"))
    mats = template["materials"]
    idmap = {}
    for cat, arr in mats.items():
        if isinstance(arr, list):
            for x in arr:
                if isinstance(x, dict) and x.get("id"):
                    idmap[x["id"]] = (cat, x)
    video_track = next(t for t in template["tracks"] if t["segments"] and idmap.get(t["segments"][0]["material_id"], ("", {}))[0] == "videos")
    sub_track = next(t for t in template["tracks"] if t["segments"] and idmap.get(t["segments"][0]["material_id"], ("", {}))[1].get("type") == "subtitle")
    video_tpl = video_track["segments"][0]
    sub_tpl = sub_track["segments"][0]
    sub_mat_tpl = idmap[sub_tpl["material_id"]][1]

    # video
    speed_ids = {s.get("id") for s in mats.get("speeds", [])}
    speed_id_for = {}
    for sm in mats.get("speeds", []):
        v = sm.get("speed")
        if v is None:
            continue
        speed_id_for.setdefault(round(float(v), 6), sm["id"])
    if abs(speed - 1.0) >= 1e-6 and round(speed, 6) not in speed_id_for:
        # no material for this speed in the template -> add one
        sm = copy.deepcopy(next(iter(mats.get("speeds") or [{}])) or {})
        sm.update({"id": uid(), "type": "speed", "speed": speed})
        mats.setdefault("speeds", []).append(sm)
        speed_id_for[round(speed, 6)] = sm["id"]
        speed_ids.add(sm["id"])
    new_video = []
    for s in segs:
        seg = copy.deepcopy(video_tpl)
        seg["id"] = uid()
        seg["source_timerange"] = {"start": int(round(s["src0"] * 1e6)), "duration": int(round((s["src1"] - s["src0"]) * 1e6))}
        seg["target_timerange"] = {"start": int(round(s["out0"] * 1e6)), "duration": int(round((s["out1"] - s["out0"]) * 1e6))}
        seg["speed"] = speed
        # JianYing takes the clip speed from the `speeds` material in
        # extra_material_refs, NOT from seg["speed"]. The template's 1.2x material
        # therefore kept re-applying 1.2x on a source that was already 1.2x: the app
        # rewrote the segment to source = target*1.2, which asks for footage past the
        # end of the file -> waveform drawn at 1.0x never matched the sped audio, and
        # near the tail the picture froze on its last frame while audio kept playing.
        refs = [r for r in (seg.get("extra_material_refs") or []) if r not in speed_ids]
        if abs(speed - 1.0) < 1e-6:
            seg.pop("speed_id", None)
        else:
            refs.append(speed_id_for.get(round(speed, 6)))
        seg["extra_material_refs"] = [r for r in refs if r]
        new_video.append(seg)
    video_track["segments"] = new_video
    # drop speed materials nothing points at (a 1.2x entry left lying around is how
    # this bug stayed invisible: seg["speed"] looked right while the material said 1.2)
    live_speed = set()
    for tr in template["tracks"]:
        for s in tr.get("segments", []):
            if s.get("speed_id"):
                live_speed.add(s["speed_id"])
            for r in s.get("extra_material_refs") or []:
                if r in speed_ids:
                    live_speed.add(r)
    mats["speeds"] = [s for s in mats.get("speeds", []) if s.get("id") in live_speed]

    meta = probe(str(source))
    # Only the material the segments actually point at may be rewritten. Rewriting
    # every template video left one orphan per template segment (26 entries for a
    # 1-segment clip, all claiming the same path/id) — a shape JianYing never writes
    # itself, and the shape that came with a waveform that did not match the sound.
    used_video_ids = {s.get("material_id") for s in new_video} - {None}
    for v in mats.get("videos", []):
        if v.get("id") not in used_video_ids:
            continue
        v["path"] = source.as_posix()
        v["material_name"] = source.name
        v["duration"] = int(round(total_src * 1e6))
        if meta.get("width"):
            v["width"], v["height"] = meta["width"], meta["height"]
    # keep exactly one material per referenced id: the template repeats the same id
    # across its segments, so filtering by id alone still leaves a pile of duplicates
    # (25 entries for a 1-segment clip). One segment -> one material is the shape
    # JianYing writes when you cut a clip by hand.
    seen_vid, kept_vid = set(), []
    for v in mats.get("videos", []):
        if v.get("id") in used_video_ids and v["id"] not in seen_vid:
            seen_vid.add(v["id"])
            kept_vid.append(v)
    mats["videos"] = kept_vid

    # subtitles
    parts = []
    subs = sorted(edl.get("subtitles", []), key=lambda x: x["start"])
    for i, line in enumerate(subs):
        o0 = map_time(float(line["start"]))
        if o0 is None:
            continue
        o1 = map_time(float(line["end"])) or o0 + 0.5
        if i + 1 < len(subs):
            n0 = map_time(float(subs[i + 1]["start"]))
            if n0 is not None and n0 > o0:
                o1 = n0  # 0-gap: previous end == next start
        o1 = max(o0 + 0.12, o1)
        chunks = split_text(str(line.get("text", "")))
        chars = sum(len(c) for c in chunks)
        cursor = o0
        for ci, chunk in enumerate(chunks):
            end = o1 if ci == len(chunks) - 1 else cursor + (o1 - o0) * len(chunk) / chars
            parts.append((cursor, end, chunk))
            cursor = end

    new_subs = []
    font_name = (preset.get("subtitle") or {}).get("font") or ""
    fixed_font = None if args.no_style_fix or not font_name else (
        find_font(font_name, [Path(args.font_cache)]) or sub_mat_tpl.get("font_path"))
    for o0, o1, text in parts:
        seg = copy.deepcopy(sub_tpl)
        seg["id"] = uid()
        seg["target_timerange"] = {"start": int(round(o0 * 1e6)), "duration": int(round((o1 - o0) * 1e6))}
        seg["extra_material_refs"] = []
        seg["render_index"] = 15000
        seg["track_render_index"] = 8
        mat = copy.deepcopy(sub_mat_tpl)
        mat["id"] = uid()
        set_sub_text(mat, text)
        if fixed_font:
            apply_subtitle_style(mat, seg, preset, fixed_font)
        mats["texts"].append(mat)
        seg["material_id"] = mat["id"]
        new_subs.append(seg)
    sub_track["segments"] = new_subs

    # titles
    # preset.title.sizes 要逐行套上：模板自带两行可能是 13/12，不套就会回落到模板字号。
    title_mats = [m for m in mats.get("texts", []) if m.get("type") == "text"]
    title_sizes = ((preset.get("title") or {}).get("sizes") or [])
    for i, (mat, text) in enumerate(zip(title_mats, edl.get("titles", [])[:2])):
        obj = load_text(mat)
        obj["text"] = text
        if obj.get("styles"):
            obj["styles"][0]["range"] = [0, len(text)]
            if i < len(title_sizes):
                for st in obj["styles"]:
                    st["size"] = float(title_sizes[i])
        mat["content"] = json.dumps(obj, ensure_ascii=False)
        mat["recognize_text"] = text
    for t in template["tracks"]:
        for seg in t.get("segments", []):
            _, mat = idmap.get(seg.get("material_id"), ("", {}))
            if mat.get("type") == "text":
                seg["target_timerange"] = {"start": 0, "duration": int(round(total * 1e6))}

    # effects and SFX
    events = edl.get("events", [])
    wanted_fx = {}
    for e in events:
        if e.get("kind") == "video_effect":
            wanted_fx.setdefault(e["name"], []).append(e)
    wanted_sfx = [e for e in events if e.get("kind") == "sfx"]
    lib_assets = (fx_lib.get("assets") or {}) if fx_lib else {}
    kept_tracks = []
    found_fx = set()
    for t in template["tracks"]:
        if not t.get("segments"):
            continue
        if t is video_track:
            kept_tracks.append(t)
            continue
        _, mat = idmap.get(t["segments"][0].get("material_id"), ("", {}))
        name = mat.get("name") or ""
        if name == "模糊":
            t["segments"][0]["target_timerange"] = {"start": 0, "duration": int(round(total * 1e6))}
            kept_tracks.append(t)
        elif name in wanted_fx:
            e = wanted_fx[name][0]
            at = map_time(float(e.get("at", 0))) or 0
            dur = float(e.get("duration", t["segments"][0]["target_timerange"]["duration"] / 1e6))
            t["segments"][0]["target_timerange"] = {"start": int(round(at * 1e6)), "duration": int(round(dur * 1e6))}
            found_fx.add(name)
            kept_tracks.append(t)
        elif t is sub_track or mat.get("type") == "text":
            kept_tracks.append(t)
        elif t.get("type") == "audio":
            pass
        # drop unrequested 暗角/冲刺/etc.

    def clone_asset(entry: dict):
        """Material + its extra-ref sub-materials, freshly id'd. Returns (material, seg_tpl, ref_ids)."""
        mats = template["materials"]
        mat = copy.deepcopy(entry["material_template"])
        mat["id"] = uid()
        mats.setdefault(entry["material_category"], []).append(mat)
        ref_ids = []
        for ref in entry.get("refs") or []:
            rmat = copy.deepcopy(ref["material"])
            rmat["id"] = uid()
            mats.setdefault(ref["category"], []).append(rmat)
            ref_ids.append(rmat["id"])
        return mat, entry.get("segment_template") or {}, ref_ids

    # ---- video effects the template does not carry (红温大头 etc.) -> clone from library
    for name, evs in wanted_fx.items():
        if name in found_fx:
            continue
        entry = lib_assets.get(name)
        if not entry:
            print(f"WARN: {name} not in template and not in fx library; skipped")
            continue
        if entry.get("kind") != "video_effect":
            print(f"WARN: {name} 在 fx library 里是 {entry.get('kind')}，"
                  f"但这一批事件按 video_effect 找它；skipped —— 是 manual 里的 kind 写错了")
            continue
        if not entry.get("path_exists", True):
            print(f"WARN: {name} 的资源包不在剪映缓存里（{entry['path']}）；"
                  f"放进来只会是空特效。先跑 build_fx_library.py --repair 或在剪映里手动用一次")
            continue
        for e in evs:
            at = map_time(float(e.get("at", 0)))
            if at is None:
                print(f"WARN: event {name} @ {e.get('at')} falls in a deleted span; skipped")
                continue
            dur = float(e.get("duration", entry.get("duration_s") or 2.5))
            mat, seg_tpl, _ = clone_asset(entry)
            if not mat.get("value"):
                mat["value"] = 1.0
            seg = copy.deepcopy(seg_tpl)
            seg["id"] = uid()
            seg["material_id"] = mat["id"]
            seg["target_timerange"] = {"start": int(round(at * 1e6)), "duration": int(round(dur * 1e6))}
            seg["render_index"] = 11000 + len([x for x in kept_tracks if x.get("flag") == 2])
            track = {"id": uid(), "type": entry.get("track_type") or "mixed", "flag": 2,
                     "is_default_name": True, "segments": [seg]}
            kept_tracks.append(track)
        print(f"cloned effect {name} x{len(evs)} from fx library ({entry['from_timeline']})")

    # ---- SFX: prefer the template's own audio material, else clone from the library
    audio_assets = {}
    for t in template["tracks"]:
        if t.get("type") != "audio":
            continue
        for seg in t.get("segments", []):
            _, mat = idmap.get(seg.get("material_id"), ("", {}))
            name = mat.get("name")
            if name and name not in audio_assets:
                audio_assets[name] = (mat, seg)
    audio_track = next((t for t in template["tracks"] if t.get("type") == "audio"), None)
    if wanted_sfx:
        if audio_track is None:
            audio_track = {"id": uid(), "type": "audio", "is_default_name": True, "segments": []}
            kept_tracks.append(audio_track)
        audio_track["segments"] = []
        for e in wanted_sfx:
            name = e.get("name")
            at = map_time(float(e.get("at", 0)))
            if at is None:
                print(f"WARN: sfx {name} @ {e.get('at')} falls in a deleted span; skipped")
                continue
            dur = float(e.get("duration", 1.7))
            if name in audio_assets:
                _, tpl_seg = audio_assets[name]
                seg = copy.deepcopy(tpl_seg)
                seg["id"] = uid()
            else:
                entry = lib_assets.get(name)
                if not entry:
                    print(f"WARN: no audio material named {name} in template or fx library; skipped")
                    continue
                if entry.get("kind") != "sfx":
                    print(f"WARN: {name} 在 fx library 里是 {entry.get('kind')}，"
                          f"但这一批事件按 sfx 找它；skipped —— 是 manual 里的 kind 写错了")
                    continue
                if not entry.get("path_exists", True):
                    print(f"WARN: {name} 的音效文件不在剪映缓存里；跳过（先跑 build_fx_library.py --repair）")
                    continue
                mat, seg_tpl, ref_ids = clone_asset(entry)
                dur = min(dur, round((mat.get("duration") or 0) / 1e6, 3)) or dur
                seg = copy.deepcopy(seg_tpl)
                seg["id"] = uid()
                seg["material_id"] = mat["id"]
                seg["extra_material_refs"] = ref_ids
                print(f"cloned sfx {name} from fx library ({entry['from_timeline']})")
            seg["target_timerange"] = {"start": int(round(at * 1e6)), "duration": int(round(dur * 1e6))}
            seg["source_timerange"] = {"duration": int(round(dur * 1e6))}
            audio_track["segments"].append(seg)
        if audio_track["segments"] and audio_track not in kept_tracks:
            kept_tracks.append(audio_track)

    kept_tracks = [t for t in kept_tracks if t is not sub_track]
    # An EDL with no subtitles means "hand this to the creator to recognise in
    # JianYing" — appending an empty text track would just leave a dead lane.
    if sub_track.get("segments"):
        kept_tracks.append(sub_track)
    template["tracks"] = kept_tracks

    # 只留下真正被段子引用的画面特效。模板里躺着 暗角/冲刺 这类没用到的特效素材，
    # 剪映打开草稿照样会去 Cache 找它们的包：2026-11-02 剪映升到 11.7 后把 11.6 的
    # 特效包清掉了，check_resources 就一直报 missing:1（冲刺）。第二道要加特效是从
    # assets/fx_library.json 按名字克隆的，不靠草稿里预先躺这一份。
    used_refs = set()
    for t in kept_tracks:
        for seg in t.get("segments", []):
            if seg.get("material_id"):
                used_refs.add(seg["material_id"])
            for r in seg.get("extra_material_refs") or []:
                used_refs.add(r)
    ve = template["materials"].get("video_effects")
    if isinstance(ve, list):
        dropped = [m.get("name") for m in ve
                   if isinstance(m, dict) and m.get("id") not in used_refs]
        template["materials"]["video_effects"] = [
            m for m in ve
            if not isinstance(m, dict) or m.get("id") in used_refs]
        if dropped:
            print("  pruned unused video_effects: " + ", ".join(str(d) for d in dropped))

    template["duration"] = int(round(total * 1e6))
    template["id"] = uid()
    template["name"] = edl.get("name", "untitled")
    now = int(time.time() * 1e6)
    template["create_time"] = now
    template["update_time"] = now

    # self-check: cloned assets must be fully wired, or JianYing shows a broken/empty effect.
    # (duplicate ids are NOT a defect - a real JianYing draft reuses ids across categories,
    #  so the only meaningful checks are: references resolve, every requested event landed,
    #  and every cloned resource file is actually on disk.)
    known = set()
    by_id = {}
    for cat, arr in template["materials"].items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    known.add(m["id"])
                    by_id.setdefault(m["id"], m)
    dangling = []
    placed = {}
    for t in template["tracks"]:
        for s in t.get("segments") or []:
            mid = s.get("material_id")
            if mid and mid not in known:
                dangling.append(f"{t.get('type')} segment -> missing material {mid}")
            for r in s.get("extra_material_refs") or []:
                if r not in known:
                    dangling.append(f"{t.get('type')} segment -> missing ref {r}")
            mat = by_id.get(mid) or {}
            if mat.get("name") and (mat.get("type") in ("sound", "video_effect", "face_effect", "effect")):
                placed[mat["name"]] = placed.get(mat["name"], 0) + 1
    missing_events = []
    for e in events:
        want = e.get("name")
        got = placed.get(want, 0)
        need = sum(1 for x in events if x.get("name") == want)
        if got < need:
            missing_events.append(f"{want}: 期望 {need} 个，草稿里只有 {got} 个")
    dead_paths = sorted({(m.get("name"), m.get("path")) for m in by_id.values()
                         if m.get("path") and m.get("type") in ("sound", "video_effect", "face_effect")
                         and not Path(str(m["path"]).replace("\\", "/")).exists()})

    for x in dangling[:8]:
        print("WARN dangling reference:", x)
    for x in missing_events:
        print("WARN event not placed:", x)
    for name, pth in dead_paths:
        print(f"WARN resource file missing: {name} -> {pth}")
    if not dangling and not missing_events and not dead_paths:
        print(f"integrity: ok — {len(placed)} 类音效/特效全部落位，引用完整，资源文件在盘上")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "draft_content.json").write_text(json.dumps(template, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.meta_template and Path(args.meta_template).exists():
        m = json.loads(Path(args.meta_template).read_text(encoding="utf-8"))
    else:
        m = {}
    m.update({
        "draft_name": template["name"],
        "draft_fold_path": outdir.as_posix(),
        "draft_root_path": outdir.parent.as_posix(),
        "draft_cover": "draft_cover.jpg",
        "draft_id": uid(),
        "tm_duration": template["duration"],
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "draft_new_version": "189.0.0",
        "draft_removable_storage_device": "D:",
        "draft_need_rename_folder": False,
    })
    (outdir / "draft_meta_info.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(source), "-frames:v", "1",
         "-vf", "scale=360:640", str(outdir / "draft_cover.jpg")],
        check=False,
    )
    print(f"built {outdir} duration={total:.1f}s segments={len(segs)} subs={len(new_subs)} tracks={len(template['tracks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
