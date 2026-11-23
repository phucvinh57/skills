import json
import os
import unittest

from tests.support import DataDirCase

from jp import answers, state
from jp.answers import normalize_key
from jp.cli import Fail
from jp.commands import track

BASICS = {
    "name": "Vinh Nguyen Phuc", "headline": "Backend Engineer", "email": "vinh@example.com", "phone": "0373000000",
    "location": "Ho Chi Minh City, Vietnam", "github": "github.com/phucvinh57", "linkedin": "linkedin.com/in/npvinh57",
    "website": "",
}
CV = {"basics": BASICS, "summary": ["4+ years of Rust"], "skills": [], "experience": [], "projects": [],
      "education": [], "certifications": [], "languages": []}


class Keys(DataDirCase):
    def test_normalize_key_ignores_case_and_punctuation(self):
        self.assertEqual(normalize_key("  Notice Period? *"), "notice period")
        self.assertEqual(normalize_key("work_authorization"), "work authorization")

    def test_an_answer_with_the_same_key_is_replaced_in_place(self):
        answers.save("Notice period?", "1 month")
        answers.save("Salary", "Negotiable")
        answers.save("notice period", "2 weeks")
        self.assertEqual(answers.pairs(), [("notice period", "2 weeks"), ("Salary", "Negotiable")])

    def test_answers_commands(self):
        code, out = self.run_cli("answers", "set", "--question", "Notice period?", "--answer", "2 weeks")
        self.assertEqual((code, out["key"]), (0, "notice period"))
        code, out = self.run_cli("answers", "get", "--question", "NOTICE PERIOD")
        self.assertEqual(out["answer"]["answer"], "2 weeks")
        code, out = self.run_cli("answers", "list")
        self.assertEqual([row["question"] for row in out["answers"]], ["Notice period?"])
        code, out = self.run_cli("answers", "forget", "--question", "notice period")
        self.assertTrue(out["forgotten"])
        code, out = self.run_cli("answers", "get", "--question", "notice period")
        self.assertFalse(out["found"])
        code, out = self.run_cli("answers", "set", "--question", "??", "--answer", "x")
        self.assertEqual(code, 1)


class WithJobs(DataDirCase):
    def make(self, company, role="Backend Engineer", status="applied", apply_url="", source=""):
        made = state.new_job(company, role, source=source, apply_url=apply_url)
        for step in ("cv_ready", "submitting", "applied", "interview", "rejected", "offer"):
            if status in ("draft", "failed"):
                break
            state.set_status(made["slug"], step, force=True)
            if step == status:
                break
        if status == "failed":
            state.set_status(made["slug"], "failed")
        return made["slug"]

    def kinds(self, slug):
        return [event["kind"] for event in state.timeline(slug)]


class Board(WithJobs):
    def test_board_groups_by_stage_and_leaves_dismissed_out(self):
        self.make("Draft Co", status="draft")
        self.make("Ready Co", status="cv_ready")
        self.make("Failed Co", status="failed")
        self.make("Sent Co", status="applied")
        self.make("Talk Co", status="interview")
        self.make("No Co", status="rejected")
        self.make("Yes Co", status="offer")
        gone = self.make("Gone Co", status="cv_ready")
        self.run_cli("track", "dismiss", gone, "--reason", "too far")
        code, out = self.run_cli("track", "board")
        self.assertEqual(code, 0)
        self.assertEqual([stage["stage"] for stage in out["stages"]], ["CVs", "Applied", "In progress", "Done"])
        by_stage = {stage["stage"]: sorted(item["company"] for item in stage["jobs"]) for stage in out["stages"]}
        self.assertEqual(by_stage, {"CVs": ["Draft Co", "Failed Co", "Ready Co"], "Applied": ["Sent Co"],
                                    "In progress": ["Talk Co"], "Done": ["No Co", "Yes Co"]})
        self.assertEqual(out["dismissed"], 1)
        sent = out["stages"][1]["jobs"][0]
        self.assertEqual((sent["status_label"], sent["days_since_update"], sent["last_event"]["kind"]), ("Applied", 0, "status"))

    def test_days_since_update(self):
        slug = self.make("Old Co", status="applied")
        path = os.path.join(self.data_dir, "jobs", slug, "job.json")
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        saved["updated_at"] = "2020-01-01T00:00:00Z"
        self.write(os.path.join("jobs", slug, "job.json"), json.dumps(saved))
        self.assertGreater(track.card(state.load_job(slug))["days_since_update"], 365)

    def test_status_moves_forward_only(self):
        slug = self.make("Acme", status="applied")
        code, out = self.run_cli("track", "status", slug, "interview", "--message", "phone screen booked")
        self.assertEqual((code, out["job"]["status"], out["from"]), (0, "interview", "applied"))
        code, out = self.run_cli("track", "status", slug, "applied")
        self.assertEqual((code, out["status"]), (1, "interview"))
        code, out = self.run_cli("track", "status", slug, "applied", "--force")
        self.assertEqual((code, out["job"]["status"]), (0, "applied"))
        self.assertIn("(forced)", state.timeline(slug)[-1]["message"])
        code, out = self.run_cli("track", "status", slug, "dismissed")
        self.assertEqual(code, 1)

    def test_note_and_timeline(self):
        slug = self.make("Acme", status="applied")
        code, _ = self.run_cli("track", "note", slug, "recruiter  called,\n wants references")
        self.assertEqual(code, 0)
        code, out = self.run_cli("track", "timeline", slug)
        self.assertEqual((out["events"][-1]["kind"], out["events"][-1]["message"]), ("note", "recruiter called, wants references"))
        self.assertEqual(self.run_cli("track", "note", "nope", "x")[0], 1)

    def test_interview_rounds(self):
        slug = self.make("Acme", status="applied")
        code, out = self.run_cli("track", "interview", slug)
        self.assertEqual((out["job"]["status"], out["interview_round"]), ("interview", 1))
        code, out = self.run_cli("track", "interview", slug)
        self.assertEqual(out["interview_round"], 2)
        code, out = self.run_cli("track", "interview", slug, "--round", 4)
        self.assertEqual(out["interview_round"], 4)
        before = len(state.timeline(slug))
        self.run_cli("track", "interview", slug, "--round", 4)
        self.assertEqual(len(state.timeline(slug)), before, "the same round again is a no-op")
        self.assertEqual(state.timeline(slug)[-1]["message"], "Round: 4")
        done = self.make("Offer Co", status="offer")
        self.assertEqual(self.run_cli("track", "interview", done)[0], 1)

    def test_dismiss_and_restore(self):
        slug = self.make("Acme", status="failed")
        code, out = self.run_cli("track", "dismiss", slug, "--reason", "too far")
        self.assertEqual((code, out["dismissed_from"]), (0, "failed"))
        saved = state.load_job(slug)
        self.assertEqual((saved["status"], saved["dismissed_from"], saved["dismiss_reason"]), ("dismissed", "failed", "too far"))
        # Left only by Restore.
        self.assertEqual(self.run_cli("track", "status", slug, "cv_ready", "--force")[0], 1)
        self.assertEqual(self.run_cli("apply", "done", slug, "--applied")[0], 1)
        code, out = self.run_cli("track", "restore", slug)
        self.assertEqual((code, out["restored_to"]), (0, "failed"))
        saved = state.load_job(slug)
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("dismissed_from", saved)
        self.assertEqual(self.run_cli("track", "restore", slug)[0], 1)

    def test_dismissed_is_reached_only_before_a_submit(self):
        for status in ("submitting", "applied", "interview", "rejected", "offer"):
            slug = self.make(f"{status} co", status=status)
            code, out = self.run_cli("track", "dismiss", slug)
            self.assertEqual(code, 1, status)
            self.assertEqual(state.load_job(slug)["status"], status)
        for status in ("draft", "cv_ready", "failed"):
            slug = self.make(f"{status} co", status=status)
            self.assertEqual(self.run_cli("track", "dismiss", slug)[0], 0, status)


class Apply(WithJobs):
    def setUp(self):
        super().setUp()
        state.write_json("profile.json", {"cv": CV})
        answers.save("Notice period", "2 weeks")
        self.slug = self.make("Acme", status="cv_ready", apply_url="https://jobs.lever.co/acme/abc-123")
        state.write_json(os.path.join("jobs", self.slug, "cv.json"), CV)
        state.write_text(os.path.join("jobs", self.slug, "jd.md"), "We build things in Rust. 3 openings.\n" + "x" * 5000 + " TAILEND")
        state.write_json(os.path.join("jobs", self.slug, "analysis.json"), {"location": "Remote", "must_have": ["Rust"], "key_terms": ["tokio"]})
        self.cv_pdf = state.write_text(os.path.join("jobs", self.slug, "cv.pdf"), "%PDF")

    def test_start(self):
        code, out = self.run_cli("apply", "start", self.slug)
        self.assertEqual(code, 0)
        self.assertEqual((out["apply_url"], out["cv_file"]), ("https://jobs.lever.co/acme/abc-123", self.cv_pdf))
        self.assertEqual(out["basics"]["email"], "vinh@example.com")
        self.assertEqual([row["answer"] for row in out["remembered_answers"]], ["2 weeks"])
        self.assertIn("explicit yes", out["next"])
        self.assertEqual(state.load_job(self.slug)["status"], "cv_ready", "start does not change the status")

    def test_start_needs_a_rendered_cv_and_never_invents_an_address(self):
        draft = self.make("Draft Co", status="draft")
        self.assertEqual(self.run_cli("apply", "start", draft)[0], 1)
        bare = self.make("Bare Co", status="cv_ready", source="pasted text")
        self.assertEqual(self.run_cli("apply", "start", bare)[0], 1, "no cv.pdf / cv.html")
        state.write_text(os.path.join("jobs", bare, "cv.html"), "<html>")
        code, out = self.run_cli("apply", "start", bare)
        self.assertEqual((code, out["apply_url"]), (0, None))
        self.assertTrue(out["cv_file"].endswith("cv.html"))
        sent = self.make("Sent Co", status="applied")
        self.assertEqual(self.run_cli("apply", "start", sent)[0], 1)

    def test_done_transitions(self):
        # Straight from CV ready (the user applied by hand, no form was read).
        code, out = self.run_cli("apply", "done", self.slug, "--applied")
        self.assertEqual((out["from"], out["status"]), ("cv_ready", "applied"))
        # Again: nothing changes; a failure after it was sent is refused.
        code, out = self.run_cli("apply", "done", self.slug, "--applied")
        self.assertEqual((code, out["changed"]), (0, False))
        self.assertEqual(self.run_cli("apply", "done", self.slug, "--failed")[0], 1)
        self.assertEqual(state.load_job(self.slug)["status"], "applied")

        failed = self.make("Fail Co", status="cv_ready")
        code, out = self.run_cli("apply", "done", failed, "--failed", "--reason", "the form kept rejecting the phone")
        self.assertEqual(out["status"], "failed")
        self.assertIn("rejecting the phone", state.timeline(failed)[-1]["message"])
        # Failed -> applied goes through submitting.
        code, out = self.run_cli("apply", "done", failed, "--applied")
        self.assertEqual(out["status"], "applied")
        messages = [event["message"] for event in state.timeline(failed)]
        self.assertTrue(any(message.startswith("failed -> submitting") for message in messages))
        self.assertFalse(any("(forced)" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
