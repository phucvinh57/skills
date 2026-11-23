#!/usr/bin/env python3
"""Transcribe a YouTube URL or local video/audio into .md, .txt, .srt, or .json.

YouTube: uses its English captions; falls back to local Whisper if none exist.
Local media: local Whisper. Caption files (.vtt/.srt/Whisper .json): converted directly.
"""
import argparse
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CAPTIONS = {".vtt", ".srt", ".json"}


def seconds(value):
    parts = value.replace(",", ".").split(":")
    return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))


def stamp(value, sep="."):
    ms = round(value * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{sep}{ms % 1000:03}"


def parse(path):
    """Read captions or Whisper JSON into [{start, end, text}], merging rolling duplicates."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        cues = data["segments"] if isinstance(data, dict) else data
    else:
        cues = []
        for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
            lines = block.splitlines()
            for i, line in enumerate(lines):
                match = re.match(r"^\s*([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
                if match:
                    cues.append({"start": seconds(match[1]), "end": seconds(match[2]),
                                 "text": " ".join(lines[i + 1:])})
                    break
    result, previous, previous_end = [], [], -1.0
    for cue in cues:
        words = html.unescape(re.sub(r"<[^>]*>", "", cue["text"])).split()
        if not words:
            continue
        # YouTube auto-captions repeat the previous line's tail; drop it when cues overlap in time.
        overlap = 0
        if cue["start"] < previous_end:
            overlap = next((n for n in range(min(len(previous), len(words)), 0, -1)
                            if previous[-n:] == words[:n]), 0)
        if overlap == len(words):
            result[-1]["end"] = max(result[-1]["end"], cue["end"])
        else:
            result.append({"start": float(cue["start"]), "end": float(cue["end"]),
                           "text": " ".join(words[overlap:])})
        previous, previous_end = words, cue["end"]
    if not result:
        raise ValueError(f"No timestamped speech found in {path}")
    return result


def render(cues, suffix):
    if suffix == ".json":
        return json.dumps(cues, ensure_ascii=False, indent=2) + "\n"
    if suffix == ".srt":
        return "\n".join(f"{i}\n{stamp(c['start'], ',')} --> {stamp(c['end'], ',')}\n{c['text']}\n"
                         for i, c in enumerate(cues, 1))
    if suffix == ".md":
        return "".join(f"[{stamp(c['start'])[:8]}] {c['text']}\n" for c in cues)
    if suffix == ".txt":  # paragraph break after a pause of 2s or more
        paragraphs = [[cues[0]["text"]]]
        for previous, cue in zip(cues, cues[1:]):
            if cue["start"] - previous["end"] >= 2:
                paragraphs.append([])
            paragraphs[-1].append(cue["text"])
        return "\n\n".join(" ".join(p) for p in paragraphs) + "\n"
    raise ValueError("Output must end in .md, .txt, .srt, or .json")


def whisper(media, workdir, model):
    subprocess.run(["whisper", str(media), "--language", "en", "--task", "transcribe", "--model", model,
                    "--output_format", "json", "--output_dir", str(workdir)], check=True)
    return workdir / f"{Path(media).stem}.json"


def youtube(url, workdir, model):
    base = ["yt-dlp", "--no-playlist", "-o", str(workdir / "source.%(ext)s")]
    subprocess.run(base + ["--skip-download", "--write-subs", "--write-auto-subs",
                           "--sub-langs", "en,en-US,en-GB,en-orig", "--sub-format", "vtt", "--", url], check=True)
    subtitles = sorted(workdir.glob("source*.vtt"))
    if subtitles:
        return subtitles[0]
    subprocess.run(base + ["-f", "ba", "-x", "--audio-format", "m4a", "--", url], check=True)
    return whisper(workdir / "source.m4a", workdir, model)


def transcribe(source, workdir, model="base.en"):
    if re.match(r"^https?://", source):
        return parse(youtube(source, workdir, model))
    path = Path(source).resolve()
    return parse(path if path.suffix.lower() in CAPTIONS else whisper(path, workdir, model))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="YouTube URL, local media, or caption file")
    parser.add_argument("output", help="Output file: .md, .txt, .srt, or .json")
    parser.add_argument("--model", default="base.en", help="Whisper model (default: base.en)")
    args = parser.parse_args()
    output = Path(args.output)
    try:
        if output.exists():
            raise ValueError(f"{output} already exists")
        render([{"start": 0, "end": 1, "text": "x"}], output.suffix.lower())  # fail fast on bad extension
        with tempfile.TemporaryDirectory() as tmp:
            cues = transcribe(args.source, Path(tmp), args.model)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render(cues, output.suffix.lower()), encoding="utf-8")
        print(f"Wrote {len(cues)} cues to {output}")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
