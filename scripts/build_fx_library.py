#!/usr/bin/env python3
"""Build a local sound/effect library from the user's own JianYing timelines.

Why: `build_draft.py` can only place an effect whose material already exists in the
reference template. The creator's manual fine-cuts introduced sounds and effects the
template never carried (综艺亮了或提示音 / 咻，抛物弹簧音 / 噗，短放屁声 / 红温大头 ...),
so "add the right sound to this line" was impossible to generate — only to document.

This scans every timeline of the user's project, keeps the **full material payload plus
the segment template and its extra-ref sub-materials** for each named sound/effect, and
drops it into a JSON the builder can clone from by name.

    python build_fx_library.py --plain <dir of decrypted draft_content.json> \
        --out ../assets/fx_library.json [--jy-draftc ... --jy-dir ... --project ...]

Fields intentionally stripped when cloning (recorded in `_stripped`):

  request_id            per-session trace id, meaningless in a new draft
  algorithm_artifact_path  points at ANOTHER timeline's folder
                        (##_draftpath_placeholder_<uuid>_##/video_effect/multi_faces_algorithm/...)
                        Leaving it in makes JianYing look for a file that is not ours.

Face effects (红温大头) still need a visual check in JianYing: the engine re-runs face
detection, and we cannot verify that from JSON.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

VIDEO_EFFECT_CATS = ("video_effects",)
SOUND_CATS = ("audios",)
STRIP = ("request_id", "algorithm_artifact_path")
# effect names the fixed template already carries: still recorded, but flagged
TEMPLATE_BUILTINS = {"模糊", "暗角", "冲刺", "水滴声"}


def to_s(us: int | float) -> float:
    return round(float(us) / 1e6, 3)


def load_content(path: Path, jy_draftc: str | None, jy_dir: str | None) -> dict:
    """Read a draft_content.json whether it is plaintext or still encrypted."""
    raw = path.read_bytes()
    if raw[:1] == b"{":
        return json.loads(raw.decode("utf-8"))
    if not jy_draftc:
        return None            # mixed corpus: skip what we cannot read instead of dying
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / path.parent.name
        work.mkdir()
        (work / path.name).write_bytes(raw)
        cmd = [jy_draftc, "-dc", str(work), "--keep-flag"]
        if jy_dir:
            cmd += ["--jy-dir", jy_dir]
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out_path = work / path.name
        if not out_path.exists() or out_path.read_bytes()[:1] != b"{":
            raise SystemExit(f"decrypt failed for {path}")
        return json.loads(out_path.read_text(encoding="utf-8"))


def index(materials: dict) -> dict:
    idx = {}
    for cat, arr in materials.items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idx[m["id"]] = (cat, m)
    return idx


def collect(content: dict, source: str, out: dict, missing: list) -> None:
    idx = index(content.get("materials") or {})
    for track in content.get("tracks") or []:
        for seg in track.get("segments") or []:
            cat, mat = idx.get(seg.get("material_id"), ("", {}))
            if not mat or mat.get("type") in ("text", "subtitle"):
                continue
            name = mat.get("name")
            if not name:
                continue
            is_sound = cat in SOUND_CATS and mat.get("type") == "sound"
            is_effect = cat in VIDEO_EFFECT_CATS
            if not (is_sound or is_effect):
                continue
            path = mat.get("path") or ""
            exists = bool(path) and Path(path.replace("\\", "/")).exists()
            if not exists:
                missing.append({"name": name, "path": path, "source": source})
            entry = {
                "name": name,
                "kind": "sfx" if is_sound else "video_effect",
                "material_category": cat,
                "material_type": mat.get("type"),
                "effect_id": mat.get("effect_id"),
                "resource_id": mat.get("resource_id"),
                "apply_target_type": mat.get("apply_target_type"),
                "value": mat.get("value"),
                "duration_s": to_s(mat.get("duration") or 0),
                "path": path,
                "path_exists": exists,
                "in_fixed_template": name in TEMPLATE_BUILTINS,
                "from_timeline": source,
                "track_type": track.get("type"),
                "segment_template": {k: v for k, v in seg.items()
                                     if k not in ("id", "material_id", "extra_material_refs",
                                                  "target_timerange", "source_timerange")},
                "refs": [],
                "material_template": {k: v for k, v in copy.deepcopy(mat).items() if k != "id"},
            }
            for ref in seg.get("extra_material_refs") or []:
                rcat, rmat = idx.get(ref, ("", {}))
                if rmat:
                    entry["refs"].append({"category": rcat,
                                          "material": {k: v for k, v in copy.deepcopy(rmat).items() if k != "id"}})
            for f in STRIP:
                entry["material_template"].pop(f, None)
            entry["_stripped"] = list(STRIP)
            prev = out.get(name)
            # keep the best copy: prefer one whose resource file still exists
            if prev is None or (not prev.get("path_exists") and entry["path_exists"]):
                out[name] = entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plain", required=True,
                    help="decrypted draft dir(s), comma separated; searched recursively for draft_content.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jy-draftc", default=None)
    ap.add_argument("--jy-dir", default=None)
    ap.add_argument("--only-missing", action="store_true", help="keep only assets absent from the fixed template")
    ap.add_argument("--cache-root", default=None, help="JianYing Cache root for --repair")
    ap.add_argument("--repair", action="store_true",
                    help="re-download missing effect packages from JianYing's resource DB (network write into the app cache)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    out: dict = {}
    missing: list = []
    skipped = 0
    for spec in [x.strip() for x in args.plain.split(",") if x.strip()]:
        root = Path(spec)
        files = [root] if root.is_file() else sorted(root.glob("draft_content.json")) or             sorted(root.glob("**/draft_content.json"))
        if not files:
            print(f"WARN: no draft_content.json under {spec}")
            continue
        for f in files:
            # label = project + timeline when the corpus is nested, else the folder id
            parts = f.relative_to(root).parts if root.is_dir() else ()
            source = "__".join(x.replace("-plain", "")[:24] for x in parts[:-1]) or f.parent.name[:8]
            content = load_content(f, args.jy_draftc, args.jy_dir)
            if content is None:
                skipped += 1
                continue
            collect(content, source, out, missing)
    if skipped:
        print(f"跳过 {skipped} 个未解密文件（传 --jy-draftc/--jy-dir 可一并解）")

    if args.repair:
        import hashlib
        import urllib.request
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from check_resources import default_cache_root, find_package_entry, install_package  # noqa: E402
        cache_root = Path(args.cache_root) if args.cache_root else default_cache_root()
        for name, e in out.items():
            if e["path_exists"]:
                continue
            if e["kind"] == "sfx":
                # sounds are a single mp3 under Cache/music/<md5>.mp3, not an extracted package
                md5 = Path(e["path"]).name.split(".")[0]
                entry = find_package_entry(cache_root, e.get("resource_id"), e.get("effect_id"))
                dest = cache_root / "music" / f"{md5}.mp3"
                if not entry or not entry.get("url") or dest.exists():
                    print(f"  补不了 {name}: 资源库里查不到声音包（只能在剪映里手动用一次这个音效）")
                    continue
                urllib.request.urlretrieve(entry["url"], dest)
                got = hashlib.md5(dest.read_bytes()).hexdigest()
                if entry.get("md5") and got != entry["md5"]:
                    print(f"  补包失败 {name}: md5 不符（{got} != {entry['md5']}），已删除")
                    dest.unlink(missing_ok=True)
                    continue
                e["path_exists"] = True
                e["repaired"] = dest.as_posix()
                print(f"  补回 {name} -> {dest}")
                continue
            entry = find_package_entry(cache_root, e.get("resource_id"), e.get("effect_id"))
            if not entry or not entry.get("url"):
                print(f"  补不了 {name}: 资源库里查不到（只能在剪映里手动加一次这个特效，然后重建库）")
                continue
            try:
                dest = install_package(entry, cache_root)
                e["path_exists"] = dest.exists()
                e["repaired"] = dest.as_posix()
                print(f"  补回 {name} -> {dest}")
            except SystemExit as err:
                print(f"  补包失败 {name}: {err}")
        missing = [m for m in missing if not out.get(m["name"], {}).get("path_exists")]

    if args.only_missing:
        out = {k: v for k, v in out.items() if not v.get("in_fixed_template")}
    lib = {
        "_about": "sounds/effects cloned from the creator's own timelines; keyed by material name. "
                  "build_draft.py --fx-library reads this to place assets the template does not carry.",
        "_stripped_fields": list(STRIP),
        "_warning": "path must exist in the JianYing Cache at edit time; run check_resources.py after building. "
                    "face effects (红温大头) need a visual check in JianYing.",
        "assets": out,
    }
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")
    sfx = [k for k, v in out.items() if v["kind"] == "sfx"]
    fx = [k for k, v in out.items() if v["kind"] == "video_effect"]
    print(f"{dest}  assets={len(out)}  sfx={len(sfx)}  effects={len(fx)}")
    for k, v in sorted(out.items()):
        tag = "模板已有" if v["in_fixed_template"] else "新增可克隆"
        ok = "ok " if v["path_exists"] else "缺文件"
        print(f"  [{ok}] {k}  {v['kind']}/{v['material_type']}  {v['duration_s']}s  <- {v['from_timeline']}  ({tag})")
    uniq_missing = {(m["name"], m["path"]): m for m in missing}.values()
    if uniq_missing:
        print(f"\n资源缺失 {len(uniq_missing)} 个素材（名字能写进 EDL，但剪映里不会响/不会显示）：")
        for m in sorted(uniq_missing, key=lambda x: x["name"]):
            print("  ", m["name"], "|", m["path"][-58:])
        print("  修复：加 --repair 从剪映资源库补包；查不到的只能在剪映里手动用一次该素材后重建库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
