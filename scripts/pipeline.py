#!/usr/bin/env python3
"""Livestream clip pipeline: cut clips by SRT timestamps (FFmpeg) then remove
silence/breaths (auto-editor) in one deterministic pass.

Manifest = TSV, one clip per line:  start<TAB>end<TAB>name
  start/end : SRT timestamps, hh:mm:ss,ms or hh:mm:ss.ms (comma or dot both OK)
  name      : output base name WITHOUT extension, e.g. 01-转行收入账
  Lines starting with '#', 'start\t'/'起始' headers, or blank are skipped.

Usage:
  python pipeline.py --src REC.mp4 --manifest clips.tsv --outdir OUTDIR \
      [--margin 0.2s] [--edit "audio:threshold=0.005"] [--keep-raw] [--dry-run]
"""
import argparse, glob, os, subprocess, sys, shutil

try:  # keep Chinese clip names readable on GBK consoles
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def find_ae(cli_path):
    """Locate the auto-editor executable (or a wrapper script)."""
    if cli_path:
        return cli_path
    ae = shutil.which("auto-editor")
    if ae:
        return ae
    pats = [
        os.path.join(os.environ.get("APPDATA", ""),
                     "Python", "Python3*", "Scripts", "auto-editor.exe"),
        os.path.expanduser("~/.local/bin/auto-editor"),
        "/usr/local/bin/auto-editor", "/usr/bin/auto-editor",
    ]
    for p in pats:
        hits = glob.glob(p)
        if hits:
            return hits[0]
    sys.exit("ERROR: auto-editor not found. Install: python -m pip install auto-editor"
             "  (or pass --ae /path/to/ae.sh)")


def find_ffmpeg():
    """Return the directory containing ffmpeg/ffprobe."""
    ff = shutil.which("ffmpeg")
    if not ff:
        hits = glob.glob(os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft/WinGet/Packages", "Gyan.FFmpeg*",
            "**", "bin", "ffmpeg.exe"), recursive=True)
        ff = hits[0] if hits else None
    if not ff:
        sys.exit("ERROR: ffmpeg not found. Install: winget install Gyan.FFmpeg"
                 " (or get ffmpeg from https://ffmpeg.org and add to PATH)")
    return os.path.dirname(ff)


def to_sec(ts):
    """'00:01:02,500' -> 62.5 (float). Accepts comma or dot, hh:mm:ss(.ms)."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, (m, s) = "0", parts
    else:
        raise ValueError(f"bad timestamp: {ts!r}")
    return int(h) * 3600 + int(m) * 60 + float(s)


def duration_of(path, env):
    """Seconds via ffprobe; returns float or None."""
    probe = shutil.which("ffprobe", path=env.get("PATH")) or os.path.join(
        env["FFDIR"], "ffprobe" + (".exe" if os.name == "nt" else ""))
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, env=env).stdout.strip()
        return float(out)
    except Exception:
        return None


def mmss(sec):
    if sec is None:
        return "?"
    m, s = divmod(int(round(sec)), 60)
    return f"{m:02d}:{s:02d}"


def parse_manifest(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            raw = ln.rstrip("\n")
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            cols = raw.split("\t")
            if len(cols) < 3:
                continue
            start, end, name = cols[0], cols[1], "\t".join(cols[2:])
            if start.lower().startswith(("start", "起始", "入")):
                continue  # header row
            rows.append((start.strip(), end.strip(), name.strip()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--ae", default=None,
                    help="override: path to auto-editor exe or ae.sh wrapper")
    ap.add_argument("--margin", default="0.2s",
                    help="silence margin kept around speech (auto-editor -m)")
    ap.add_argument("--edit", default=None,
                    help='auto-editor edit mode, e.g. "audio:threshold=0.005"')
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep pre-silence-cut intermediate files")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.src):
        sys.exit(f"ERROR: source not found: {a.src}")

    ae_bin = find_ae(a.ae)
    ae_prefix = ["bash", ae_bin] if ae_bin.endswith(".sh") else [ae_bin]
    ffdir = find_ffmpeg()
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + os.pathsep + ffdir
    env["FFDIR"] = ffdir

    rows = parse_manifest(a.manifest)
    if not rows:
        sys.exit("ERROR: manifest produced no clips (need start<TAB>end<TAB>name)")

    work = os.path.join(a.outdir, ".work")
    os.makedirs(a.outdir, exist_ok=True)
    if not a.dry_run:
        os.makedirs(work, exist_ok=True)

    print(f"SRC      : {a.src}")
    print(f"OUTDIR   : {a.outdir}")
    print(f"CLIPS    : {len(rows)}   margin={a.margin}\n")

    summary = []
    for i, (start, end, name) in enumerate(rows, 1):
        ss, to = to_sec(start), to_sec(end)
        dur = to - ss
        if dur <= 0:
            print(f"[{i}] SKIP {name}: end<=start ({start}..{end})")
            continue
        raw = os.path.join(work, f"{name}__raw.mp4")
        final = os.path.join(a.outdir, f"{name}.mp4")
        print(f"[{i}] {name}  {start} -> {end}  (span {mmss(dur)})")
        if a.dry_run:
            summary.append((name, dur, None, None))
            continue

        # 1) frame-accurate cut via FFmpeg (input seek + transcode)
        cut = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{ss}", "-i", a.src, "-t", f"{dur}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", raw],
            env=env)
        if cut.returncode or not os.path.isfile(raw):
            print(f"    !! ffmpeg cut FAILED rc={cut.returncode}")
            continue
        raw_dur = duration_of(raw, env)

        # 2) remove silence/breaths via auto-editor
        ae_cmd = ae_prefix + [raw, "-o", final, "--no-open", "-m", a.margin]
        if a.edit:
            ae_cmd += ["--edit", a.edit]
        trim = subprocess.run(ae_cmd, env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        if trim.returncode or not os.path.isfile(final):
            print(f"    !! auto-editor FAILED rc={trim.returncode} (kept raw)")
            shutil.copy(raw, final)
            final_dur = raw_dur
        else:
            final_dur = duration_of(final, env)
            if not a.keep_raw:
                os.remove(raw)

        removed = (raw_dur - final_dur) if raw_dur and final_dur else None
        print(f"    raw {mmss(raw_dur)}  ->  trimmed {mmss(final_dur)}"
              f"   (-{mmss(removed) if removed is not None else '?'})")
        summary.append((name, dur, raw_dur, final_dur))

    if a.dry_run:
        print("\n(dry-run: nothing written)")
        return

    print("\n=== RESULT ===")
    for name, dur, raw_dur, final_dur in summary:
        print(f"{name:<24} span {mmss(dur)}  raw {mmss(raw_dur)}  "
              f"final {mmss(final_dur)}")
    if not a.keep_raw and os.path.isdir(work):
        try:
            os.rmdir(work)
        except OSError:
            pass


if __name__ == "__main__":
    main()
