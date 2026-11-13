#!/usr/bin/env python3
"""恢复失效的剪映特效/音效素材包（本地缓存被清理时）。

剪映草稿 JSON 只存素材包的绝对路径。素材包被清理后，时间线上那个特效显示"失效"，
但它引用的 resource_id / effect_id / md5 都还在 JSON 里，而且剪映自己的资源数据库
（`Cache/ressdk_db/<账号>/rp.db` 的 `http_cache` 表）留有过当年的下载地址 ——
所以不必在剪映里重新搜一遍素材，直接按草稿记的那个 md5 把包下回原位置，
落点、蒙版、时轴全都不用动。

    python restore_fx.py --check   --content <时间线目录> --jy-draftc <exe> --jy-dir <剪映目录>
    python restore_fx.py --restore --content <同上>
    python restore_fx.py --restore --name 灵机一动 冲刺          # 不读草稿，按库里记录的名字恢复

先完全退出剪映再跑；跑完重启剪映才会重读缓存。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_resources import (default_cache_root, find_package_entry, install_package,  # noqa: E402
                             load_draft, resolve_draftc)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def collect(d: dict) -> list[dict]:
    """草稿里所有特效/音效素材，带上草稿期望的落点。"""
    out = []
    for cat, arr in (d.get("materials") or {}).items():
        if not isinstance(arr, list):
            continue
        for m in arr:
            if not isinstance(m, dict):
                continue
            kind = ("effect" if (cat in ("video_effects", "effects") or m.get("video_effect"))
                    else "sound" if m.get("type") in ("sound", "music") else None)
            if not kind:
                continue
            path = str(m.get("path") or "").replace("\\", "/")
            if not path:
                continue
            out.append({"name": m.get("name") or m.get("resource_name") or "?", "kind": kind,
                        "resource_id": str(m.get("resource_id") or ""),
                        "effect_id": str(m.get("effect_id")
                                         or (m.get("effect") or {}).get("effect_id") or ""),
                        "path": path, "md5": Path(path).name})
    return out


def status_of(it: dict, cache_root: Path, do_restore: bool) -> str:
    p = Path(it["path"])
    if it["kind"] == "sound":
        if p.exists() and p.stat().st_size > 0:
            return "OK"
        state = "音效文件不在盘上"
    else:
        if p.exists() and any(p.rglob("*")):
            if (p.parent / f"{p.name}_modity_time.txt").exists():
                return "OK"
            if not do_restore:
                return "文件齐但缺 _modity_time.txt"
            files = sorted(x for x in p.rglob("*") if x.is_file())
            lines = [f"{x}, {int(x.stat().st_mtime)}" for x in files]
            (p.parent / f"{p.name}_modity_time.txt").write_text("\n".join(lines) + "\n",
                                                                 encoding="utf-8")
            return f"已补写 modity_time（{len(files)} 个文件）"
        state = "特效包目录为空或不存在"

    entry = find_package_entry(cache_root, it["resource_id"] or None, it["effect_id"] or None,
                               md5=it.get("md5") or None, title=it.get("name") or None)
    if not entry:
        return f"{state}；rp.db 无此 resource_id 记录 → 只能在剪映素材库重新添加"
    if not entry.get("url"):
        return f"{state}；有记录但无 URL（资源已下架）→ 换一个同类特效"
    if not do_restore:
        return f"{state}；可恢复：md5={entry['md5'][:12]} 标题={entry['title']}"
    if entry["md5"] and it["md5"] and entry["md5"] != it["md5"]:
        print(f"      草稿记的 md5={it['md5'][:12]}，库里最新 md5={entry['md5'][:12]}（版本换过）")
    try:
        dest = install_package(entry, cache_root)
    except SystemExit as e:
        return f"{state}；下载失败：{e}"
    if str(dest).replace("\\", "/").lower() != it["path"].lower():
        return f"已装到 {dest}，但草稿指向 {it['path']} → 需把草稿 path 改成新落点"
    return f"已恢复到 {dest}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", help="时间线目录或 draft_content.json")
    ap.add_argument("--name", nargs="*", default=[], help="按 fx_library 里的素材名恢复（不读草稿）")
    ap.add_argument("--check", action="store_true", help="只报告，不下载")
    ap.add_argument("--restore", action="store_true", help="缺失就下载补回")
    ap.add_argument("--jy-draftc", default=None)
    ap.add_argument("--jy-dir", default=None)
    ap.add_argument("--cache-root", default=None)
    ap.add_argument("--fx-library", default=str(Path(__file__).resolve().parent.parent / "assets" / "fx_library.json"))
    a = ap.parse_args()
    if not (a.content or a.name):
        ap.error("给 --content <时间线> 或 --name <素材名…>")
    cache_root = Path(a.cache_root) if a.cache_root else default_cache_root()

    items = []
    if a.content:
        d = load_draft(Path(a.content), resolve_draftc(a.jy_draftc), a.jy_dir)
        items = collect(d)
    lib = json.loads(Path(a.fx_library).read_text(encoding="utf-8")).get("assets", {}) \
        if Path(a.fx_library).exists() else {}
    for nm in a.name:
        e = lib.get(nm)
        if not e:
            print(f"[{nm}] 不在 fx_library：先在剪映里加一次这个特效，再跑 build_fx_library.py 刷新库")
            continue
        mt = e.get("material_template") or {}
        path = str(e.get("path") or mt.get("path") or "").replace("\\", "/")
        items.append({"name": nm, "kind": "effect" if e.get("kind") == "video_effect" else "sound",
                      "resource_id": str(mt.get("resource_id") or ""),
                      "effect_id": str(mt.get("effect_id") or ""),
                      "path": path, "md5": Path(path).name})

    bad = [it for it in items if status_of(it, cache_root, bool(a.restore)) != "OK"] \
        if a.check else []
    if a.check:
        for it in bad:
            print(f"[{it['kind']}] {it['name']:<16} {status_of(it, cache_root, False)}")
        print(f"共 {len(items)} 个素材，失效 {len(bad)} 个")
        return 1 if bad else 0

    shown = set()
    for it in items:
        r = status_of(it, cache_root, bool(a.restore))
        if r == "OK":
            continue
        key = (it["name"], r)
        if key in shown:
            continue
        shown.add(key)
        print(f"[{it['kind']}] {it['name']:<16} {r}")
    print(f"共检查 {len(items)} 个素材（同名多点位已合并），处理了 {len(shown)} 种")
    return 0


if __name__ == "__main__":
    sys.exit(main())
