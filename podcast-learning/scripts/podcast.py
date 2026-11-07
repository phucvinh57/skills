#!/usr/bin/env python3
"""Portable, standard-library media helpers. Semantic decisions belong to the agent."""
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Timestamps must be finite numeric seconds")
    return float(value)


def seconds(value):
    parts = value.replace(",", ".").split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid caption timestamp: {value}")
    return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))


def stamp(value):
    milliseconds = round(value * 1000)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    sec, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02}.{ms:03}"


def clean(text):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", text)).split())


def normalize(path):
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        cues = data.get("segments") if isinstance(data, dict) else data
        if not isinstance(cues, list):
            raise ValueError("JSON must be a cue list or Whisper object with segments")
    else:
        cues = []
        # Block parsing handles cue IDs, settings, and multiline text.
        for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
            lines = block.splitlines()
            if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
                continue
            for i, line in enumerate(lines):
                match = re.match(r"^\s*([\d:.,]+)\s+-->\s+([\d:.,]+)", line)
                if match:
                    cues.append({"start": seconds(match[1]), "end": seconds(match[2]),
                                 "text": " ".join(lines[i + 1:])})
                    break
    result = []
    previous_words = []
    previous_end = -1.0
    previous_start = -1.0
    for cue in cues:
        start, end = number(cue["start"]), number(cue["end"])
        if start < 0 or end <= start or start < previous_start:
            raise ValueError("Cues must be chronological with 0 <= start < end")
        previous_start = start
        text = clean(cue["text"])
        if not text:
            continue
        words = text.split()
        # Only de-duplicate temporally overlapping rolling cues, never separate repetitions.
        overlap = 0
        if start < previous_end:
            for count in range(min(len(previous_words), len(words)), 0, -1):
                if previous_words[-count:] == words[:count]:
                    overlap = count
                    break
        if overlap == len(words):
            if result:
                result[-1]["end"] = max(result[-1]["end"], end)
        else:
            result.append({"start": start, "end": end, "text": " ".join(words[overlap:])})
        previous_words, previous_end = words, end
    if not result:
        raise ValueError("No usable timestamped speech found")
    return result


def resolve(base, value):
    path = Path(value)
    return path if path.is_absolute() else base / path


def validate(manifest):
    if manifest.get("version") != 1:
        raise ValueError("Unsupported manifest version (expected 1)")
    duration = number(manifest["source"]["duration"])
    clips = manifest["clips"]
    if duration <= 0 or not isinstance(clips, list) or not clips:
        raise ValueError("Positive source duration and nonempty clips are required")
    ids, cursor = set(), 0.0
    for clip in clips:
        identifier = clip["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{3}", identifier) or identifier in ids:
            raise ValueError("Clip IDs must be unique three-digit strings")
        ids.add(identifier)
        start, end = number(clip["start"]), number(clip["end"])
        if start != cursor or end <= start:
            raise ValueError("Clips must adjoin exactly, beginning at zero")
        if not isinstance(clip.get("title"), str) or not clip["title"].strip():
            raise ValueError("Each clip needs a title")
        length = end - start
        if duration < 900:
            if len(clips) != 1:
                raise ValueError("Short sources must have one clip")
        elif length < 900 or length > 1800:
            raise ValueError("Each clip must last 900–1800 seconds")
        cursor = end
    if cursor != duration:
        raise ValueError("Clips must cover exactly the source duration")
    return duration


def run(arguments):
    subprocess.run([str(arg) for arg in arguments], check=True)


def probe(path):
    process = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                              "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return json.loads(process.stdout)


def verify_video(path, duration, audio_required=True):
    info = probe(path)
    streams = {stream["codec_type"] for stream in info["streams"]}
    if "video" not in streams or (audio_required and "audio" not in streams):
        raise ValueError(f"Missing playback streams in {path}")
    if abs(float(info["format"]["duration"]) - duration) > 0.25:
        raise ValueError(f"Unexpected media duration in {path}")


def write_text_artifacts(directory, cues, start, end):
    subtitles, transcript = [], [f"# Transcript\n\nOriginal interval: {stamp(start)} – {stamp(end)}\n"]
    for cue in cues:
        left, right = max(start, cue["start"]), min(end, cue["end"])
        if right <= left:
            continue
        local_start, local_end = stamp(left - start), stamp(right - start)
        if local_start == local_end:
            continue
        subtitles.append(f"{len(subtitles) + 1}\n{local_start.replace('.', ',')} --> "
                         f"{local_end.replace('.', ',')}\n{cue['text']}\n")
        transcript.append(f"[{stamp(left)} original | {local_start} clip] {cue['text']}\n")
    (directory / "subtitles.srt").write_text("\n".join(subtitles), encoding="utf-8")
    (directory / "transcript.md").write_text("\n".join(transcript), encoding="utf-8")


def render(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    duration = validate(manifest)
    base = manifest_path.parent
    source = resolve(base, manifest["source"]["path"]).resolve()
    transcript_path = resolve(base, manifest["transcript"]["path"])
    cues = normalize(transcript_path)
    if max(cue["end"] for cue in cues) > duration + 1:
        raise ValueError("Transcript extends beyond source; check synchronization")
    verify_video(source, duration)
    stat = source.stat()
    identity = {"source": str(source), "size": stat.st_size, "mtime": stat.st_mtime_ns,
                "transcript": hashlib.sha256(transcript_path.read_bytes()).hexdigest(),
                "encoding": "libx264-crf23-veryfast-aac128k-v1"}
    manifest["media_status"] = "in_progress"
    write_json(manifest_path, manifest)
    for clip in manifest["clips"]:
        directory = base / "clips" / clip["id"]
        directory.mkdir(parents=True, exist_ok=True)
        video = directory / "video.mp4"
        receipt = directory / "completion.json"
        signature = dict(identity, start=clip["start"], end=clip["end"])
        length = clip["end"] - clip["start"]
        try:
            if video.exists():
                if not receipt.exists() or read_json(receipt) != signature:
                    raise ValueError(f"Existing {video} has no matching receipt; use a fresh output directory")
                verify_video(video, length)
                print(f"Reusing clip {clip['id']}", flush=True)
            else:
                partial = directory / "video.partial.mp4"
                print(f"Rendering clip {clip['id']}: {clip['title']}", flush=True)
                run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", clip["start"], "-i", source, "-t", length,
                     "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", partial])
                verify_video(partial, length)
                # Save receipt first: interruption before rename can safely rerender the partial file.
                write_json(receipt, signature)
                os.replace(partial, video)
            write_text_artifacts(directory, cues, clip["start"], clip["end"])
            clip["media_status"] = "complete"
            clip.pop("error", None)
            clip["artifacts"] = {key: str((directory / filename).relative_to(base)) for key, filename in
                                 [("video", "video.mp4"), ("subtitles", "subtitles.srt"),
                                  ("transcript", "transcript.md"), ("lesson", "lesson.md")]}
        except (ValueError, OSError, subprocess.CalledProcessError) as error:
            clip["media_status"], clip["error"] = "failed", str(error)
            manifest["media_status"] = "partial"
            write_json(manifest_path, manifest)
            raise
        write_json(manifest_path, manifest)
    manifest["media_status"] = "complete"
    write_json(manifest_path, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Report available external tools")
    normal = commands.add_parser("normalize", help="Convert captions to normalized JSON")
    normal.add_argument("input")
    normal.add_argument("output")
    for name in ("validate", "render"):
        command = commands.add_parser(name)
        command.add_argument("manifest")
    args = parser.parse_args()
    try:
        if args.command == "check":
            print(json.dumps({name: shutil.which(name) for name in
                              ("ffmpeg", "ffprobe", "yt-dlp", "whisper")}, indent=2))
        elif args.command == "normalize":
            if Path(args.output).exists():
                raise ValueError("Output already exists; choose a new normalized transcript path")
            write_json(args.output, normalize(args.input))
        elif args.command == "validate":
            duration = validate(read_json(args.manifest))
            print(f"Valid clip plan: {duration:.3f} seconds")
        else:
            render(args.manifest)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
