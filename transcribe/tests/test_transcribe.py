import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("transcribe", Path(__file__).parents[1] / "scripts" / "transcribe.py")
transcribe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transcribe)

CUES = [{"start": 0, "end": 2, "text": "Hello there."}, {"start": 5, "end": 6.5, "text": "New topic."}]


class ParseTests(unittest.TestCase):
    def test_vtt_rolling_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "captions.vtt"
            path.write_text("WEBVTT\n\nNOTE ignored\n\ncue-1\n00:00.000 --> 00:02.000 align:start\n"
                            "<c>Hello &amp; welcome</c>\n\n00:01.500 --> 00:03.000\n"
                            "Hello &amp; welcome\nto science\n\n00:02.500 --> 00:04.000\n"
                            "to science\n\n00:05.000 --> 00:06.000\nto science\n", encoding="utf-8")
            cues = transcribe.parse(path)
            self.assertEqual([c["text"] for c in cues], ["Hello & welcome", "to science", "to science"])
            self.assertEqual(cues[1]["end"], 4)

    def test_srt_and_whisper_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt = Path(tmp) / "a.srt"
            srt.write_text("1\n00:00:01,200 --> 00:00:03,400\nTwo lines\nof speech.\n", encoding="utf-8")
            self.assertEqual(transcribe.parse(srt), [{"start": 1.2, "end": 3.4, "text": "Two lines of speech."}])
            whisper = Path(tmp) / "w.json"
            whisper.write_text(json.dumps({"segments": [{"start": 1, "end": 2, "text": " A term. "}]}))
            self.assertEqual(transcribe.parse(whisper)[0]["text"], "A term.")

    def test_empty_captions_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.vtt"
            path.write_text("WEBVTT\n\nNOTE nothing\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No timestamped speech"):
                transcribe.parse(path)


class RenderTests(unittest.TestCase):
    def test_formats(self):
        self.assertIn("2\n00:00:05,000 --> 00:00:06,500\nNew topic.", transcribe.render(CUES, ".srt"))
        self.assertEqual(transcribe.render(CUES, ".md"), "[00:00:00] Hello there.\n[00:00:05] New topic.\n")
        self.assertEqual(transcribe.render(CUES, ".txt"), "Hello there.\n\nNew topic.\n")
        self.assertEqual(json.loads(transcribe.render(CUES, ".json")), CUES)
        with self.assertRaises(ValueError):
            transcribe.render(CUES, ".pdf")


class SourceTests(unittest.TestCase):
    def test_dispatch(self):
        workdir = Path("/tmp/unused")
        with patch.object(transcribe, "youtube") as youtube, patch.object(transcribe, "whisper") as whisper, \
                patch.object(transcribe, "parse") as parse:
            transcribe.transcribe("https://youtu.be/x", workdir)
            youtube.assert_called_once()
            transcribe.transcribe("talk.mp4", workdir)
            whisper.assert_called_once()
            transcribe.transcribe("talk.srt", workdir)
            self.assertEqual(whisper.call_count, 1)
            self.assertEqual(parse.call_count, 3)


if __name__ == "__main__":
    unittest.main()
