# Transcribe

A portable Agent Skill that turns a YouTube URL or local video into a transcript.

## Install

Copy the `transcribe` folder to `.claude/skills/` (Claude Code) or `.agents/skills/` (Codex), in a project or your home directory.

## Dependencies

- Python 3.10+ (standard library only).
- [yt-dlp](https://github.com/yt-dlp/yt-dlp#installation) for YouTube.
- [OpenAI Whisper](https://github.com/openai/whisper#setup) for local media, or YouTube videos without captions. The first run downloads model weights.

## Use

```sh
python3 transcribe/scripts/transcribe.py 'https://www.youtube.com/watch?v=VIDEO_ID' transcript.md
python3 transcribe/scripts/transcribe.py ./interview.mp4 interview.srt
```

## Test

```sh
python3 -m unittest discover -s transcribe/tests -v
```
