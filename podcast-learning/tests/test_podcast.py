import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("podcast", Path(__file__).parents[1] / "scripts" / "podcast.py")
podcast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(podcast)


def plan(duration=2400):
    return {"version": 1, "source": {"path": "source.mp4", "duration": duration},
            "transcript": {"path": "transcript.json", "provenance": "test"},
            "clips": [{"id": "001", "title": "First", "start": 0, "end": duration / 2},
                      {"id": "002", "title": "Second", "start": duration / 2, "end": duration}]}


class TranscriptTests(unittest.TestCase):
    def test_caption_formats_and_rolling_text(self):
        with tempfile.TemporaryDirectory(prefix="podcast space ") as tmp:
            path = Path(tmp) / "captions.vtt"
            path.write_text("WEBVTT\n\nNOTE ignored\n\ncue-1\n00:00.000 --> 00:02.000 align:start\n"
                            "<c>Hello &amp; welcome</c>\n\n00:01.500 --> 00:03.000\n"
                            "Hello &amp; welcome\nto science\n\n00:02.500 --> 00:04.000\n"
                            "to science\n\n00:05.000 --> 00:06.000\nto science\n", encoding="utf-8")
            cues = podcast.normalize(path)
            self.assertEqual([c["text"] for c in cues], ["Hello & welcome", "to science", "to science"])
            self.assertEqual(cues[1]["end"], 4)
            srt = Path(tmp) / "captions.srt"
            srt.write_text("1\n00:00:01,200 --> 00:00:03,400\nTwo lines\nof speech.\n", encoding="utf-8")
            self.assertEqual(podcast.normalize(srt), [{"start": 1.2, "end": 3.4, "text": "Two lines of speech."}])

    def test_whisper_and_invalid_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "whisper.json"
            podcast.write_json(path, {"segments": [{"start": 1, "end": 2, "text": " A term. "}]})
            self.assertEqual(podcast.normalize(path)[0]["text"], "A term.")
            for cues in ([], [{"start": 2, "end": 1, "text": "bad"}],
                         [{"start": float("nan"), "end": 2, "text": "bad"}]):
                podcast.write_json(path, cues)
                with self.assertRaises(ValueError):
                    podcast.normalize(path)
            captions = Path(tmp) / "empty.vtt"
            captions.write_text("WEBVTT\n\nNOTE No English captions available\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No usable timestamped speech"):
                podcast.normalize(captions)

    def test_offsets_and_spanning_cue(self):
        cues = [{"start": 1199, "end": 1202, "text": "Across the cut."}]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            podcast.write_text_artifacts(directory, cues, 1200, 2400)
            srt = (directory / "subtitles.srt").read_text()
            self.assertIn("00:00:00,000 --> 00:00:02,000", srt)
            self.assertIn("00:20:00.000 original | 00:00:00.000 clip", (directory / "transcript.md").read_text())
            podcast.write_text_artifacts(directory, cues, 0, 1200)
            self.assertIn("00:19:59,000 --> 00:20:00,000", (directory / "subtitles.srt").read_text())


class PlanTests(unittest.TestCase):
    def test_valid_and_short_source_exception(self):
        self.assertEqual(podcast.validate(plan()), 2400)
        short = plan(60)
        short["clips"] = [{"id": "001", "title": "Short", "start": 0, "end": 60}]
        self.assertEqual(podcast.validate(short), 60)

    def test_bad_boundaries_and_remainder(self):
        for start in (1199, 1201):
            invalid = plan()
            invalid["clips"][1]["start"] = start
            with self.assertRaises(ValueError):
                podcast.validate(invalid)
        invalid = plan()
        invalid["clips"][0]["end"] = 1700
        invalid["clips"][1]["start"] = 1700  # short 700-second tail
        with self.assertRaises(ValueError):
            podcast.validate(invalid)
        invalid["clips"][0]["end"] = 1500
        invalid["clips"][1]["start"] = 1500  # rebalance to a valid 900-second tail
        podcast.validate(invalid)

    def test_invalid_ids_and_incomplete_coverage(self):
        for identifier in ("../x", "001", 2):
            invalid = plan()
            invalid["clips"][1]["id"] = identifier
            with self.assertRaises(ValueError):
                podcast.validate(invalid)
        invalid = plan()
        invalid["clips"][-1]["end"] -= 1
        with self.assertRaises(ValueError):
            podcast.validate(invalid)


class RecoveryTests(unittest.TestCase):
    def test_interruption_resume_and_stale_source(self):
        with tempfile.TemporaryDirectory(prefix="podcast space ") as tmp:
            base = Path(tmp)
            (base / "source.mp4").write_bytes(b"source")
            podcast.write_json(base / "transcript.json", [{"start": 0, "end": 2400, "text": "Test."}])
            manifest = base / "manifest.json"
            podcast.write_json(manifest, plan())
            calls = []

            def fake_ffmpeg(arguments):
                calls.append(arguments)
                if len(calls) == 2:
                    raise subprocess.CalledProcessError(1, "ffmpeg")
                Path(arguments[-1]).write_bytes(b"video")

            with patch.object(podcast, "verify_video"), patch.object(podcast, "run", side_effect=fake_ffmpeg):
                with self.assertRaises(subprocess.CalledProcessError):
                    podcast.render(manifest)
                state = podcast.read_json(manifest)
                self.assertEqual(state["media_status"], "partial")
                self.assertEqual(state["clips"][0]["media_status"], "complete")
                podcast.render(manifest)
                self.assertEqual(len(calls), 3)  # first clip reused
                self.assertEqual(podcast.read_json(manifest)["media_status"], "complete")
                (base / "source.mp4").write_bytes(b"changed source")
                with self.assertRaisesRegex(ValueError, "matching receipt"):
                    podcast.render(manifest)

    def test_existing_unowned_video_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "source.mp4").write_bytes(b"source")
            podcast.write_json(base / "transcript.json", [{"start": 0, "end": 2, "text": "Test."}])
            manifest = base / "manifest.json"
            podcast.write_json(manifest, plan())
            directory = base / "clips" / "001"
            directory.mkdir(parents=True)
            (directory / "video.mp4").write_bytes(b"keep")
            with patch.object(podcast, "verify_video"), patch.object(podcast, "run") as runner:
                with self.assertRaisesRegex(ValueError, "matching receipt"):
                    podcast.render(manifest)
                runner.assert_not_called()
            self.assertEqual((directory / "video.mp4").read_bytes(), b"keep")

    def test_missing_media_tool(self):
        with patch.object(subprocess, "run", side_effect=FileNotFoundError("ffprobe")):
            with self.assertRaises(FileNotFoundError):
                podcast.verify_video("missing.mp4", 100)

    def test_playback_streams_and_duration(self):
        with patch.object(podcast, "probe", return_value={
                "streams": [{"codec_type": "video"}], "format": {"duration": "100"}}):
            with self.assertRaisesRegex(ValueError, "Missing playback streams"):
                podcast.verify_video("silent.mp4", 100)
        with patch.object(podcast, "probe", return_value={
                "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
                "format": {"duration": "110"}}):
            with self.assertRaisesRegex(ValueError, "Unexpected media duration"):
                podcast.verify_video("long.mp4", 100)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
class MediaIntegrationTests(unittest.TestCase):
    def test_synthetic_video_render_and_resume(self):
        with tempfile.TemporaryDirectory(prefix="podcast media ") as tmp:
            base = Path(tmp)
            podcast.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                         "color=c=blue:s=160x90:r=25:d=2", "-f", "lavfi", "-i",
                         "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                         "-shortest", base / "source.mp4"])
            info = podcast.probe(base / "source.mp4")
            duration = float(info["format"]["duration"])
            manifest = plan(duration)
            manifest["clips"] = [{"id": "001", "title": "Synthetic", "start": 0, "end": duration}]
            podcast.write_json(base / "manifest.json", manifest)
            podcast.write_json(base / "transcript.json", [{"start": 0, "end": 1, "text": "Synthetic speech."}])
            podcast.render(base / "manifest.json")
            video = base / "clips" / "001" / "video.mp4"
            podcast.verify_video(video, duration)
            before = video.stat().st_mtime_ns
            podcast.render(base / "manifest.json")
            self.assertEqual(video.stat().st_mtime_ns, before)
            self.assertTrue((video.parent / "subtitles.srt").exists())


if __name__ == "__main__":
    unittest.main()
