#!/usr/bin/env python3
"""Get what a video says and shows, so the tool it is about can be identified.

    video.py <url> --out <dir>                  caption, transcript, 8 frames
    video.py <url> --out <dir> --frames 12

Prints one JSON object: title, uploader, caption, transcript (or why there
is none), frame paths, and an error when the platform refuses the download.

Needs yt-dlp and ffmpeg. Speech comes from the platform's own captions when
there are any, otherwise from a local Whisper: whisper.cpp's `whisper-cli`
(model from $WHISPER_MODEL or common locations) or OpenAI's `whisper`
command. Nothing is uploaded anywhere. Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RESTRICTED_RE = re.compile(
    r"(isn't available to everyone|login required|log in to|private video|"
    r"sign in to confirm|age[- ]restricted|requested content is not available|"
    r"not available in your country)", re.I)

MODEL_GLOBS = [
    "/opt/homebrew/share/whisper-cpp/*.bin", "/usr/local/share/whisper-cpp/*.bin",
    "/usr/share/whisper-cpp/*.bin", "~/.cache/whisper/*.bin", "~/.cache/whisper.cpp/*.bin",
    "~/whisper.cpp/models/*.bin", "~/.local/share/whisper/*.bin",
    "~/.claude/**/ggml-*.bin", "~/models/ggml-*.bin",
]
# Prefer multilingual, then larger: creators talk in many languages, and
# "base.en" mangles anything that is not English.
MODEL_RANK = ["large-v3-turbo", "large-v3", "large", "medium", "small", "base", "tiny"]


def find_model() -> str | None:
    env = os.environ.get("WHISPER_MODEL")
    if env and Path(env).expanduser().is_file():
        return str(Path(env).expanduser())
    found: list[str] = []
    for g in MODEL_GLOBS:
        found += [p for p in glob.glob(os.path.expanduser(g), recursive=True)
                  if Path(p).name.startswith("ggml-") and os.path.getsize(p) > 10_000_000]

    def rank(p: str) -> tuple[int, int]:
        name = Path(p).name
        size = next((i for i, k in enumerate(MODEL_RANK) if k in name), len(MODEL_RANK))
        return (1 if ".en" in name else 0, size)
    return sorted(set(found), key=rank)[0] if found else None


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def vtt_to_text(vtt: str) -> str:
    lines = []
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit() \
                or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return " ".join(lines)


def transcribe(audio_src: Path, work: Path) -> tuple[str, str]:
    """(transcript, method). Empty transcript means none; method says why."""
    if not shutil.which("ffmpeg"):
        return "", "no ffmpeg"
    wav = work / "audio.wav"
    r = run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(audio_src), "-vn",
             "-ac", "1", "-ar", "16000", str(wav)])
    if r.returncode != 0 or not wav.exists():
        return "", "no audio track"
    cli = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if cli:
        model = find_model()
        if model:
            r = run([cli, "-m", model, "-f", str(wav), "-nt", "-l", "auto"], timeout=900)
            text = " ".join(l.strip() for l in r.stdout.splitlines() if l.strip())
            if text:
                return text, f"local whisper.cpp ({Path(model).name})"
    if shutil.which("whisper"):
        r = run(["whisper", str(wav), "--model", "base", "--output_format", "txt",
                 "--output_dir", str(work)], timeout=1800)
        txt = work / "audio.txt"
        if txt.exists() and txt.read_text().strip():
            return " ".join(txt.read_text().split()), "local openai-whisper"
    return "", ("no Whisper model found: set WHISPER_MODEL to a ggml-*.bin file"
                if cli else "no local Whisper installed (whisper-cli or whisper)")


def frames(video: Path, work: Path, n: int) -> list[str]:
    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video)])
    try:
        dur = float(probe.stdout.strip())
    except ValueError:
        return []
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        f = work / f"frame_{i + 1:02d}_{int(t):03d}s.jpg"
        run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=720:-2", str(f)])
        if f.exists():
            out.append(str(f))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--frames", type=int, default=8)
    a = ap.parse_args(argv)
    missing = [b for b in ("yt-dlp", "ffmpeg", "ffprobe") if not shutil.which(b)]
    if missing:
        print(json.dumps({"error": f"missing: {', '.join(missing)} (install yt-dlp and ffmpeg)"}))
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    r = run(["yt-dlp", "--no-playlist", "-o", str(a.out / "video.%(ext)s"), "--write-info-json",
             "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
             "-f", "bv*[height<=720]+ba/b[height<=720]/b", "--merge-output-format", "mp4", a.url],
            timeout=900)
    result: dict = {"url": a.url}
    info_files = list(a.out.glob("*.info.json"))
    if info_files:
        info = json.loads(info_files[0].read_text())
        result.update(title=info.get("title"), uploader=info.get("uploader"),
                      duration_s=info.get("duration"), caption=info.get("description") or "")
    videos = [p for p in a.out.glob("video.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov")]
    if not videos:
        err = (r.stderr or r.stdout).strip().splitlines()
        msg = next((l for l in reversed(err) if "ERROR" in l), err[-1] if err else "unknown error")
        result["error"] = ("login-only or restricted post; ask the user for the tool's name "
                           "or a screenshot" if RESTRICTED_RE.search(r.stderr or "") else msg[:300])
        print(json.dumps(result, indent=2))
        return 1
    video = videos[0]
    subs = sorted(a.out.glob("*.vtt"))
    if subs:
        result["transcript"], result["transcript_source"] = vtt_to_text(subs[0].read_text()), "platform captions"
    else:
        result["transcript"], result["transcript_source"] = transcribe(video, a.out)
    result["frames"] = frames(video, a.out, max(1, a.frames))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
