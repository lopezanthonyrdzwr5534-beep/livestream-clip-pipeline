#!/usr/bin/env python3
"""Check and repair the external resources referenced by a JianYing draft.

A draft JSON only stores paths to fonts, effect packages, mask packages and
media. A missing effect-mask package makes JianYing render the effect without
its mask (for example, a top blur becomes a full-frame blur).

Read-only by default. With --fix-mask-cache the script:
  1. finds the effect_mask entry in JianYing's resource DB (ressdk_db/*/rp.db),
  2. downloads the exact package from its item_urls,
  3. verifies the MD5,
  4. extracts it to Cache/effect/<effect_id>/<md5>/ and writes the manifest.

Do not copy Cache/ArtistEffect/<resource_id> blindly: the same resource_id can
have several packages (effect_mask vs complex_video_mask), with different
effect_id and MD5. Match the effect_mask category and effect_id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_content(path: Path) -> dict:
    if path.read_bytes()[:1] != b"{":
        raise ValueError("encrypted")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_draftc(value: str | None) -> str:
    if value and value != "jy-draftc":
        return value
    env = os.environ.get("JY_DRAFTC")
    if env:
        return env
    return shutil.which("jy-draftc") or (value or "jy-draftc")


def decrypt_draft(content: Path, jy_draftc: str, jy_dir: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="jy_resources_"))
    src = content.parent if content.is_file() else content
    r = subprocess.run([jy_draftc, "-dc", str(src), "-o", str(tmp),
                        "--jy-dir", jy_dir, "-q"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"decrypt failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    out = tmp / "draft_content.json"
    if not out.exists():
        raise SystemExit(f"decrypt produced no draft_content.json in {tmp}")
    return out


def default_cache_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    return Path(local) / "JianyingPro" / "User Data" / "Cache"


def load_draft(content: Path, jy_draftc: str, jy_dir: str | None) -> dict:
    if content.is_dir():
        content = content / "draft_content.json"
    try:
        return load_content(content)
    except ValueError:
        if not jy_dir:
            raise SystemExit("content is encrypted; pass --jy-dir")
        return json.loads(decrypt_draft(content, jy_draftc, jy_dir).read_text(encoding="utf-8"))


def iter_rp_dbs(cache_root: Path) -> list[Path]:
    root = cache_root / "ressdk_db"
    if not root.is_dir():
        return []
    out = list(root.glob("*.db"))
    out += list(root.glob("*/rp.db"))
    return sorted(set(out))


def find_mask_entry(cache_root: Path, resource_id: str, expected_effect_id: str | None = None) -> dict | None:
    """Find the effect_mask package metadata in JianYing's http_cache."""
    for db in iter_rp_dbs(cache_root):
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            rows = con.execute(
                "select response_body from http_cache "
                "where response_body like '%effect_mask%' and response_body like ?",
                (f"%{resource_id}%",),
            ).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for (body,) in rows:
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                continue
            for item in (data.get("data") or {}).get("effect_item_list") or []:
                ca = item.get("common_attr") or {}
                if str(ca.get("id")) != str(resource_id):
                    continue
                extra = str(ca.get("extra") or "") + str(ca.get("biz_extra") or "")
                if "effect_mask" not in extra:
                    continue
                effect_id = str(ca.get("effect_id") or "")
                if expected_effect_id and effect_id != expected_effect_id:
                    continue
                urls = ca.get("item_urls") or []
                return {
                    "resource_id": str(resource_id),
                    "effect_id": effect_id,
                    "md5": str(ca.get("md5") or ""),
                    "url": urls[0] if urls else "",
                    "title": ca.get("title") or "",
                    "extra": extra,
                }
    return None


def find_package_entry(cache_root: Path, resource_id: str | None = None,
                       effect_id: str | None = None,
                       md5: str | None = None, title: str | None = None) -> dict | None:
    """Look up any effect package (mask, filter, face effect, sound) in JianYing's http_cache.

    Same table as find_mask_entry but without the effect_mask requirement: the creator's
    library may reference 聚光灯/满屏问号/飞速计算 packages that got purged from Cache.

    Several items can carry the same common_attr.id (observed: 调色所有小项 and 星形 both
    under id 7416258989467374091, with different effect_id/md5). Returning the first hit
    installs the WRONG package, so score every candidate and prefer the one whose md5
    matches what the draft expects, then a self-consistent effect_id, then the title.
    """
    rid, eid = str(resource_id or ""), str(effect_id or "")
    needle = rid or eid
    if not needle:
        return None
    want_md5, want_title = str(md5 or ""), str(title or "")
    best, best_score = None, -1
    for db in iter_rp_dbs(cache_root):
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            rows = con.execute("select response_body from http_cache where response_body like ?",
                               (f"%{needle}%",)).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for (body,) in rows:
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                continue
            items = (data.get("data") or {}).get("effect_item_list") or []
            if isinstance(items, dict):
                items = [items]
            cands = [item.get("common_attr") or {} for item in items]
            # The same id also shows up in other response shapes (search results, nested
            # lists) where the item is NOT under data.effect_item_list. Walk the whole
            # document for anything that looks like a package record, otherwise the only
            # candidate found can be a different asset that merely shares the id.
            def walk(o):
                if isinstance(o, dict):
                    if o.get("md5") and (o.get("item_urls") or o.get("url")):
                        cands.append(o)
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(data)
            for ca in cands:
                if needle not in (str(ca.get("id") or ""), str(ca.get("effect_id") or "")):
                    continue
                if rid and str(ca.get("id")) != rid and eid and str(ca.get("effect_id")) != eid:
                    continue
                urls = ca.get("item_urls") or []
                if not urls and ca.get("url"):
                    urls = [ca["url"]]
                cand = {"resource_id": str(ca.get("id") or ""),
                        "effect_id": str(ca.get("effect_id") or ca.get("id") or ""),
                        "md5": str(ca.get("md5") or ""),
                        "url": urls[0] if urls else "",
                        "title": ca.get("title") or ca.get("name") or "",
                        "extra": str(ca.get("extra") or "")}
                score = 0
                if want_md5 and cand["md5"] == want_md5:
                    score += 8
                if rid and cand["effect_id"] == rid:
                    score += 4
                if want_title and cand["title"] == want_title:
                    score += 2
                if cand["url"]:
                    score += 1
                if score > best_score:
                    best, best_score = cand, score
    return best

def install_package(entry: dict, cache_root: Path) -> Path:
    effect_id, md5, url = entry["effect_id"], entry["md5"], entry["url"]
    if not (effect_id and md5 and url):
        raise SystemExit("resource DB entry has no effect_id/md5/item_urls")
    dest = cache_root / "effect" / effect_id / md5
    tmp = Path(tempfile.mkdtemp(prefix="jy_pkg_"))
    zip_path = tmp / "package.zip"
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        raise SystemExit(f"download failed: {e}")
    got = hashlib.md5(zip_path.read_bytes()).hexdigest()
    if got != md5:
        raise SystemExit(f"MD5 mismatch: expected {md5}, got {got}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    manifest = dest.parent / f"{md5}_modity_time.txt"
    lines = [f"{p}, {int(p.stat().st_mtime)}" for p in sorted(dest.rglob("*")) if p.is_file()]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True, help="draft_content.json or draft folder")
    ap.add_argument("--jy-draftc", default=os.environ.get("JY_DRAFTC", "jy-draftc"))
    ap.add_argument("--jy-dir", default=os.environ.get("JY_INSTALL_DIR"))
    ap.add_argument("--cache-root", default=str(default_cache_root()))
    ap.add_argument("--fix-mask-cache", action="store_true",
                    help="download the exact effect_mask package from JianYing's resource DB")
    args = ap.parse_args()

    args.jy_draftc = resolve_draftc(args.jy_draftc)
    if not (Path(args.jy_draftc).exists() or shutil.which(args.jy_draftc)):
        raise SystemExit(f"找不到 jy-draftc（{args.jy_draftc}）；加 --jy-draftc <exe 路径> "
                         f"或设环境变量 JY_DRAFTC，否则读不了剪映 11.x 的密文草稿")
    d = load_draft(Path(args.content), args.jy_draftc, args.jy_dir)
    cache_root = Path(args.cache_root)

    refs = []

    def add(path: str | None, kind: str, name: str, resource_id: str | None = None):
        if path:
            refs.append({"path": str(path).replace("\\", "/"), "kind": kind, "name": name,
                         "resource_id": resource_id})

    for arr in (d.get("materials") or {}).values():
        if not isinstance(arr, list):
            continue
        for m in arr:
            if not isinstance(m, dict):
                continue
            name = m.get("name") or m.get("material_name") or ""
            if m.get("video_effect") or m.get("type") == "video_effect":
                add(m.get("path"), "effect", name, m.get("resource_id"))
                for mask in m.get("effect_mask") or []:
                    if isinstance(mask, dict):
                        add(mask.get("path"), "mask", f"{name}/{mask.get('name','')}",
                            mask.get("resource_id") or mask.get("constant_material_id"))
            elif m.get("type") == "sticker":
                add(m.get("path"), "sticker", name, m.get("resource_id"))
            elif m.get("path"):
                kind = "video" if m.get("type") == "video" else "audio" if m.get("type") == "sound" else "asset"
                add(m.get("path"), kind, name, m.get("resource_id"))
            if m.get("font_path"):
                add(m.get("font_path"), "font", name)

    missing, fixed, notes = [], [], []
    for r in refs:
        p = Path(r["path"])
        if p.exists():
            continue
        r["missing"] = True
        if r["kind"] == "mask" and args.fix_mask_cache and r.get("resource_id"):
            expected_effect_id = p.parent.name if p.parent.parent.name == "effect" else None
            entry = find_mask_entry(cache_root, str(r["resource_id"]), expected_effect_id)
            if entry:
                dest = install_package(entry, cache_root)
                fixed.append((r, dest, entry))
                if Path(r["path"]) != dest:
                    notes.append(f"draft expects {r['path']}, package installed at {dest}; update effect_mask.path if needed")
                continue
            notes.append(f"no effect_mask entry in rp.db for resource_id={r['resource_id']}")
        missing.append(r)

    print(f"resources: {len(refs)}  missing: {len(missing)}  fixed: {len(fixed)}")
    for r in missing:
        print(f"MISSING [{r['kind']}] {r['name']}\n  {r['path']}")
        if r["kind"] == "mask" and r.get("resource_id"):
            entry = find_mask_entry(cache_root, str(r["resource_id"]))
            if entry:
                print(f"  effect_mask: effect_id={entry['effect_id']} md5={entry['md5']}")
                print(f"  url: {entry['url'] or '(empty)'}")
            else:
                print(f"  no effect_mask entry for resource_id={r['resource_id']}")
    for r, dest, entry in fixed:
        print(f"FIXED [{r['kind']}] {r['name']} -> {dest} (effect_id={entry['effect_id']})")
    for n in notes:
        print("NOTE", n)
    if missing:
        print("\nDo not copy Cache/ArtistEffect by resource_id alone; it may be a different package family.")
        print("Re-run with --fix-mask-cache to install the exact effect_mask package, then fully restart JianYing.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
