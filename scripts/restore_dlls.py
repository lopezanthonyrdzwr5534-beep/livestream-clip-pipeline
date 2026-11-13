#!/usr/bin/env python3
"""Mirror a superseded Jianying version dir so jy-draftc can load videoeditor.dll again.

Why this exists: when JianYing auto-upgrades (11.6 -> 11.7), the updater renames the
old version's binaries to `<name>_d_<hash>`. `jy-draftc` then fails with
`videoeditor.dll not found` (dir default) or `LoadLibraryExW(videoeditor.dll) failed,
gle=126` (dependencies missing). The draft cipher is unchanged across those versions,
so restoring the old names in a private mirror is enough — nothing gets written back
to the JianYing install.

    python restore_dlls.py --src <JianYing-install-dir>/<version> --dst <dll-mirror-dir>
    jy-draftc -dc <timeline_dir> --jy-dir <dst> -f -q

Copy-only on the source; re-running skips files already mirrored at the right size.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RENAMED = re.compile(r"^(.+?(?:\.exe|\.dll|\.dat))_d_[0-9a-f]+$", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="superseded JianYing version directory")
    ap.add_argument("--dst", required=True, help="private mirror to create")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src, dst = pathlib.Path(a.src), pathlib.Path(a.dst)
    if not src.is_dir():
        raise SystemExit(f"no such source dir: {src}")
    dst.mkdir(parents=True, exist_ok=True)

    n_copy = n_skip = n_dir = 0
    for p in sorted(src.iterdir()):
        if p.is_dir():
            n_dir += 1
            continue
        m = RENAMED.match(p.name)
        target = dst / m.group(1) if m else dst / p.name
        if target.exists() and target.stat().st_size == p.stat().st_size:
            n_skip += 1
            continue
        if not a.dry_run:
            shutil.copy2(p, target)
        n_copy += 1

    dll = dst / "videoeditor.dll"
    print(f"copied {n_copy}, kept {n_skip}, skipped dirs {n_dir} -> {dst}")
    print(f"videoeditor.dll present: {dll.exists()} ({dll.stat().st_size:,} bytes)"
          if dll.exists() else "videoeditor.dll MISSING -> this mirror will not load")
    if a.dry_run:
        print("(dry-run: nothing written)")
    print("now use it as:  --jy-dir " + str(dst).replace("\\", "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
