#!/usr/bin/env python3
"""Check a decrypted reference timeline against the fixed slice-edit preset."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PRESET = {
    "canvas": [1080, 1920],
    "speed": 1.2,
    "color_name": "调色所有小项",
    "color_value": 0.8,
    "blur_name": "模糊",
    "subtitle_font": "细体-思源黑体7号.otf",
    "subtitle_size": 12,
    "subtitle_color": "#FFFFFF",
    "subtitle_y": -0.236,
    "title_font": "仓耳丰黑.ttf",
    "title_sizes": {13, 12},
}


def basename(path: str) -> str:
    return Path(str(path).replace("\\", "/")).name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True,
                    help="reference/installed timeline folder (plaintext or JianYing 11.x cipher)")
    ap.add_argument("--jy-draftc", default=None, help="jy-draftc.exe, needed only for encrypted input")
    ap.add_argument("--jy-dir", default=os.environ.get("JY_INSTALL_DIR") or "",
                    help="JianYing version dir holding videoeditor.dll")
    ap.add_argument("--allow-no-subtitles", action="store_true",
                    help="handoff mode: an empty subtitle track is intended, not a defect")
    args = ap.parse_args()
    content = Path(args.template) / "draft_content.json"
    if not content.exists():
        raise SystemExit(f"missing {content}")
    if content.read_bytes()[:1] != b"{":
        # Stage 5 runs this against the *installed* copy, which is cipher text.
        from check_resources import decrypt_draft, resolve_draftc
        dc = resolve_draftc(args.jy_draftc)
        if not (Path(dc).exists() or shutil.which(dc)):
            raise SystemExit(f"草稿是密文，需要 jy-draftc 解密；找不到（{dc}）——"
                             f"加 --jy-draftc <exe 路径> 或设环境变量 JY_DRAFTC")
        if not args.jy_dir or not (Path(args.jy_dir) / "videoeditor.dll").exists():
            raise SystemExit("草稿是密文，解密需要 --jy-dir 指向含 videoeditor.dll 的剪映版本目录，"
                             f"例如 <JianYing-install-dir>/<version>（当前: {args.jy_dir!r}）")
        content = decrypt_draft(content, dc, args.jy_dir)
    d = json.loads(content.read_text(encoding="utf-8"))
    mats = d.get("materials", {})
    idmap = {}
    for cat, arr in mats.items():
        if isinstance(arr, list):
            for x in arr:
                if isinstance(x, dict) and x.get("id"):
                    idmap[x["id"]] = (cat, x)

    critical = []
    warnings = []
    canvas = d.get("canvas_config", {})
    w, h = canvas.get("width"), canvas.get("height")
    print(f"canvas : {w}x{h}")
    if [w, h] != PRESET["canvas"]:
        critical.append("canvas")

    video_tracks = []
    subtitle_segments = []
    title_segments = []
    for t in d.get("tracks", []):
        segs = t.get("segments", [])
        if not segs:
            continue
        cat, mat = idmap.get(segs[0].get("material_id"), ("", {}))
        if cat == "videos":
            video_tracks.append((t, mat))
        if mat.get("type") == "subtitle":
            subtitle_segments.extend(segs)
        if mat.get("type") == "text":
            title_segments.extend(segs)

    speeds = Counter()
    for t, _ in video_tracks:
        for s in t.get("segments", []):
            speeds[round(float(s.get("speed") or 1.0), 3)] += 1
    print(f"speed  : {dict(speeds)}")
    if not speeds:
        critical.append("video track")
    elif set(speeds) != {PRESET["speed"]}:
        warnings.append(f"speed variants {dict(speeds)}")

    # JianYing takes the clip speed from the `speeds` material in
    # extra_material_refs, NOT from seg["speed"]. A template's 1.2x material left in
    # those refs once re-sped an already-1.2x file: the picture and waveform drifted
    # apart and the tail froze while audio kept playing. seg["speed"] said 1.0 the
    # whole time, so this check has to look at the refs.
    sp_by_id = {s.get("id"): s.get("speed") for s in mats.get("speeds", [])}
    for t, _ in video_tracks:
        for s in t.get("segments", []):
            tg = s.get("target_timerange") or {}
            sr = s.get("source_timerange") or {}
            if not tg.get("duration") or not sr.get("duration"):
                continue
            ratio = sr["duration"] / tg["duration"]
            refs = [r for r in (s.get("extra_material_refs") or []) if r in sp_by_id]
            if refs:
                ref_speed = round(float(sp_by_id[refs[0]] or 1.0), 3)
                if abs(ref_speed - ratio) > 0.02:
                    critical.append(
                        f"speed ref {ref_speed}x vs 时间轴比值 {ratio:.3f}"
                        f"（段 {str(s.get('id'))[:8]}）—— 剪映会按 ref 播，画面/音频会漂")

    colors = [x for x in mats.get("effects", []) if x.get("name") == PRESET["color_name"]]
    print(f"color  : {PRESET['color_name']} x{len(colors)} values={sorted({str(x.get('value')) for x in colors})}")
    if not colors:
        critical.append("color effect")
    elif not any(abs(float(x.get("value") or 0) - PRESET["color_value"]) < 1e-6 for x in colors):
        warnings.append("color value != 0.8")

    blurs = [x for x in mats.get("video_effects", []) if x.get("name") == PRESET["blur_name"]]
    print(f"blur   : {PRESET['blur_name']} x{len(blurs)}")
    if not blurs:
        critical.append("blur effect")
    else:
        for b in blurs[:3]:
            mask = (b.get("effect_mask") or [{}])[0].get("config") or {}
            print(f"         mask h={mask.get('height')} centerY={mask.get('centerY')}")

    fonts = Counter()
    sizes = Counter()
    colors_txt = Counter()
    ys = Counter()
    for s in subtitle_segments:
        _, mat = idmap.get(s.get("material_id"), ("", {}))
        fonts[basename(mat.get("font_path", ""))] += 1
        sizes[str(mat.get("font_size"))] += 1
        colors_txt[str(mat.get("text_color"))] += 1
        clip = s.get("clip") or {}
        tr = clip.get("transform") or {}
        ys[round(float(tr.get("y") or 0), 3)] += 1
    print(f"subtitle: fonts={dict(fonts)} sizes={dict(sizes)} colors={dict(colors_txt)} y={dict(ys)}")
    if not subtitle_segments:
        # Handoff mode: the draft is delivered deliberately subtitle-free so the
        # creator can recognise subtitles in JianYing at final speed. That is not
        # a template violation, so say so instead of failing the whole check.
        if getattr(args, "allow_no_subtitles", False):
            print("subtitle: (none by design — 交接给创作者在剪映里识别)")
        else:
            critical.append("subtitle track")
    else:
        if PRESET["subtitle_font"] not in fonts:
            warnings.append("subtitle font differs")
        if str(PRESET["subtitle_size"]) not in sizes and f"{PRESET['subtitle_size']}.0" not in sizes:
            warnings.append("subtitle size differs")
        if PRESET["subtitle_color"] not in colors_txt:
            warnings.append("subtitle color differs")
        if not any(abs(y - PRESET["subtitle_y"]) < 0.03 for y in ys):
            warnings.append("subtitle y differs")

    title_fonts = Counter()
    title_sizes = Counter()
    title_ys = Counter()
    for s in title_segments:
        _, mat = idmap.get(s.get("material_id"), ("", {}))
        title_fonts[basename(mat.get("font_path", ""))] += 1
        title_sizes[str(mat.get("font_size"))] += 1
        clip = s.get("clip") or {}
        tr = clip.get("transform") or {}
        title_ys[round(float(tr.get("y") or 0), 3)] += 1
    print(f"title  : fonts={dict(title_fonts)} sizes={dict(title_sizes)} y={dict(title_ys)}")
    if not title_segments:
        critical.append("title track")
    else:
        if PRESET["title_font"] not in title_fonts:
            warnings.append("title font differs")
        if not {int(float(x)) for x in title_sizes} & PRESET["title_sizes"]:
            warnings.append("title sizes differ")

    trans = mats.get("transitions", [])
    stickers = mats.get("stickers", [])
    print(f"misc   : transitions={len(trans)} stickers={len(stickers)}")
    if trans or stickers:
        warnings.append("transitions/stickers present (fixed template expects none)")

    print("\nRESULT:", "PASS" if not critical else "FAIL")
    for c in critical:
        print("  CRITICAL:", c)
    for w_ in warnings:
        print("  WARN:", w_)
    return 0 if not critical else 1


if __name__ == "__main__":
    raise SystemExit(main())
