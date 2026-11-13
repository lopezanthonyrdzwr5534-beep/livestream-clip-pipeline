#!/usr/bin/env python3
"""Append a generated draft as a new timeline (02+) in an existing project.

JianYing 11.x stores timeline content **encrypted** on disk. install used to write
plaintext draft_content.json, which left every delivered timeline in a state the app
had to re-save before it would trust it (and on a project where it never re-saves,
simply invisible). So the plain draft is now staged, bound to the registry id,
encrypted with jy-draftc, and the cipher file is what lands in Timelines/<id>/.

Order matters: the id/name rewrite has to happen *before* encryption. Encrypting the
builder's own draft leaves the timeline carrying a id the registry does not know.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def resolve_draftc(value: str | None) -> str:
    if value and value != "jy-draftc":
        return value
    env = os.environ.get("JY_DRAFTC")
    if env:
        return env
    return shutil.which("jy-draftc") or (value or "jy-draftc")


def run(cmd) -> tuple[str, int]:
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return ((p.stdout or "") + (p.stderr or "")), p.returncode


def encrypt_in_place(tdir: Path, draftc: str, jy_dir: str, work: Path) -> bool:
    """Cipher-ify tdir/draft_content.json via a staging copy. True on success."""
    stage = work / tdir.name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(tdir / "draft_content.json", stage / "draft_content.json")
    for extra in ("draft_cover.jpg", "draft_meta_info.json"):
        if (tdir / extra).exists():
            shutil.copy2(tdir / extra, stage / extra)
    out, rc = run([draftc, "-ec", stage, "--jy-dir", jy_dir, "-f", "-q"])
    cipher = Path(str(stage) + "-enc") / "draft_content.json"
    if rc != 0 or not cipher.exists():
        print(f"ERROR: encrypt failed for {tdir.name} (rc={rc}): {out[-300:]}")
        return False
    (tdir / "draft_content.json").write_bytes(cipher.read_bytes())
    return True


def verify(tdir: Path, expect_id: str, expect_dur: object, draftc: str, jy_dir: str,
           work: Path) -> str | None:
    """Decrypt what we just wrote and confirm it is the timeline the registry points at."""
    stage = work / ("verify_" + tdir.name)
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(tdir, stage)
    out, rc = run([draftc, "-dc", stage, "--jy-dir", jy_dir, "-f", "-q"])
    plain = Path(str(stage) + "-plain") / "draft_content.json"
    if rc != 0 or not plain.exists():
        return f"回读解密失败 (rc={rc}): {out[-200:]}"
    d = json.loads(plain.read_text(encoding="utf-8"))
    if str(d.get("id", "")).upper() != expect_id.upper():
        return f"draft id {d.get('id')} != registry id {expect_id}"
    if expect_dur not in (None, "") and d.get("duration") and \
            abs(float(d["duration"]) - float(expect_dur)) > 1e6:
        return f"duration {float(d['duration'])/1e6:.1f}s != {float(expect_dur)/1e6:.1f}s"
    shutil.rmtree(stage, ignore_errors=True)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="JianYing project folder containing Timelines/")
    ap.add_argument("--content", required=True, help="generated draft_content.json or draft folder")
    ap.add_argument("--name", required=True, help="new timeline name")
    ap.add_argument("--cover", default=None, help="optional cover jpg")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--jy-draftc", default=None, help="path to jy-draftc.exe (default: $JY_DRAFTC / PATH)")
    ap.add_argument("--jy-dir", default=os.environ.get("JY_INSTALL_DIR") or "",
                    help="JianYing version dir holding videoeditor.dll")
    ap.add_argument("--no-encrypt", action="store_true",
                    help="leave draft_content.json as plaintext (only for pre-11.x projects)")
    ap.add_argument("--sort", action="store_true",
                    help="keep 时间线01 first and order the rest by name (序号=推荐名次)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project = Path(args.project)
    timelines = project / "Timelines"
    proj_path = timelines / "project.json"
    if not proj_path.exists():
        raise SystemExit(f"missing {proj_path}")
    content = Path(args.content)
    src_dir = content if content.is_dir() else content.parent
    content = src_dir / "draft_content.json"
    if not content.exists():
        raise SystemExit(f"missing {content}")

    draftc = resolve_draftc(args.jy_draftc)
    encrypt = not args.no_encrypt
    if encrypt:
        dll = Path(args.jy_dir or "") / "videoeditor.dll"
        if not Path(draftc).exists() and shutil.which(draftc) is None:
            raise SystemExit(f"找不到 jy-draftc（{draftc}）；装密文必须用它。"
                             f"确认路径或加 --no-encrypt")
        if not args.jy_dir or not dll.exists():
            raise SystemExit(f"--jy-dir 要指向含 videoeditor.dll 的剪映版本目录，"
                             f"例如 <JianYing-install-dir>/<version>（当前: {args.jy_dir!r}）")

    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    main_id = proj.get("main_timeline_id")
    if not main_id:
        raise SystemExit("project.json has no main_timeline_id")
    if any(t.get("name") == args.name for t in proj.get("timelines", [])):
        raise SystemExit(f"timeline name already exists: {args.name}")

    new_id = str(uuid.uuid4()).upper()
    now = int(time.time() * 1e6)
    d = json.loads(content.read_text(encoding="utf-8"))
    d["id"] = new_id
    d["name"] = args.name
    dur = d.get("duration")
    entry = {
        "create_time": now,
        "id": new_id,
        "is_marked_delete": False,
        "name": args.name,
        "update_time": now,
    }
    proj.setdefault("timelines", []).append(entry)
    proj["update_time"] = now
    if args.sort:
        head = [t for t in proj["timelines"] if t["id"] == main_id]
        rest = sorted((t for t in proj["timelines"] if t["id"] != main_id),
                      key=lambda t: t.get("name", ""))
        proj["timelines"] = head + rest

    print(f"project : {project}")
    print(f"main    : {main_id}")
    print(f"new id  : {new_id}")
    print(f"name    : {args.name}")
    print(f"encrypt : {'yes' if encrypt else 'no (plaintext)'}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    # unique per run: second-granularity names silently overwrote each other across a
    # batch install, including the one backup that held the pre-install state.
    backup = Path(args.backup_dir) if args.backup_dir else project.parent / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(proj_path, backup / f"project_{time.strftime('%Y%m%d_%H%M%S')}_{now}_{uuid.uuid4().hex[:6]}.json")

    tdir = timelines / new_id
    tdir.mkdir(parents=True, exist_ok=False)
    (tdir / "draft_content.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    cover = Path(args.cover) if args.cover else src_dir / "draft_cover.jpg"
    if cover.exists():
        shutil.copy2(cover, tdir / "draft_cover.jpg")
    if (src_dir / "draft_meta_info.json").exists():
        shutil.copy2(src_dir / "draft_meta_info.json", tdir / "draft_meta_info.json")

    proj_path.write_text(json.dumps(proj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    problems = []
    if encrypt:
        work = Path(tempfile.mkdtemp(prefix="jy_install_"))
        try:
            if not encrypt_in_place(tdir, draftc, args.jy_dir, work):
                problems.append("encryption")
            else:
                err = verify(tdir, new_id, dur, draftc, args.jy_dir, work)
                if err:
                    problems.append(err)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    state = (tdir / "draft_content.json").read_bytes()[:1]
    kind = "plaintext" if state == b"{" else "encrypted"

    if problems:
        print(f"installed WITH PROBLEMS: {tdir}")
        for p in problems:
            print("  -", p)
        return 1
    shutil.copy2(proj_path, timelines / "project.json.bak")
    print(f"installed: {tdir}  [{kind}, 回读校验通过]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
