# Podcast learning

A portable Agent Skill for turning long English podcasts into topic-based videos and vocabulary lessons. Requires an agent with local command execution, such as Claude Code or Codex.

## Install

Copy the entire `podcast-learning` folder into either location:

| Host | Project installation | Personal installation |
| --- | --- | --- |
| Claude Code | `.claude/skills/podcast-learning/` | `~/.claude/skills/podcast-learning/` |
| Codex | `.agents/skills/podcast-learning/` | `~/.agents/skills/podcast-learning/` |

Both installations use identical files. No plugin, API key, or vendor-specific frontmatter is required. See the official [Claude Code skill guide](https://code.claude.com/docs/en/skills) and [Codex skill guide](https://learn.chatgpt.com/docs/build-skills).

## Dependencies

- Python 3.10 or newer; helper scripts use only its standard library.
- `ffmpeg` and `ffprobe` on PATH, with H.264 (`libx264`) and AAC encoders. Install FFmpeg using your OS package manager.
- For YouTube, install [yt-dlp](https://github.com/yt-dlp/yt-dlp#installation), including its recommended JavaScript runtime components for current YouTube support.
- Optional: install [OpenAI Whisper](https://github.com/openai/whisper#setup) in a compatible separate Python environment for local transcription. Whisper has its own Python and model requirements; a newer Python that runs this helper may not support Whisper.

Check availability:

```sh
python3 /path/to/podcast-learning/scripts/podcast.py check
```

No dependency installation is performed by this skill. Network access is required for YouTube downloads and uncached Whisper models. No cloud transcription is used.

## Use

In Claude Code invoke `/podcast-learning`; in Codex invoke `$podcast-learning`, followed by your request. For example:

> Use podcast-learning on https://www.youtube.com/watch?v=VIDEO_ID. Make 15–30 minute topic-based clips and B1–B2 English lessons, with 20–25 vocabulary terms and two examples per term. Save to ./podcast-study/my-episode.

> Use podcast-learning on ./interview.mp4 with ./interview.en.srt. Explain vocabulary in simple English.

Each clip has an MP4 video, English SRT subtitles, a timestamped transcript, and a Markdown vocabulary lesson. The output directory also contains an episode index, normalized transcript, and resumable manifest. Keep generated media outside this skill directory. Full episodes can require several gigabytes and substantial rendering time.

## Verify the helpers

```sh
python3 -m unittest discover -s /path/to/podcast-learning/tests -v
```

The media integration test runs only when FFmpeg and ffprobe are available. Tests create synthetic media in temporary directories and never download podcasts.
