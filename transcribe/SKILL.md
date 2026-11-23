---
name: transcribe
description: Transcribe a YouTube URL or local video/audio file into an English transcript (.md, .txt, .srt, or .json). Use when the user wants a transcript, subtitles, or captions of a video.
---

# Transcribe

Run one command:

```sh
python3 <skill-dir>/scripts/transcribe.py SOURCE OUTPUT
```

- `SOURCE`: YouTube URL, local video/audio, or an existing caption file (.vtt, .srt, Whisper .json).
- `OUTPUT`: format comes from the extension. `.md` = timestamped lines (default choice), `.txt` = plain paragraphs, `.srt` = subtitles, `.json` = cues.
- `--model`: Whisper model, default `base.en`.

YouTube uses its English captions first, then falls back to local Whisper. Local media always uses Whisper.

If it fails, report the actual error. Missing tools: point to [README.md](README.md); do not install anything. Never invent transcript text.
