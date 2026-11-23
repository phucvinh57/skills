"""`apply`: what one application needs, and how it ended. The script never opens a page and never
submits: the agent (with a browser tool) or the user does, and only after the user's explicit yes."""
import os
import re

from .. import answers, state
from ..cli import Fail, command, group

SENT = ("applied", "interview", "rejected", "offer")


def cv_file(slug):
    """The rendered CV to attach: `cv.pdf`, else `cv.html`; None when neither exists."""
    for name in ("cv.pdf", "cv.html"):
        path = os.path.join(state.job_dir(slug), name)
        if os.path.isfile(path):
            return path
    return None


def apply_url(job):
    """The address to apply at: the job's own, else the analysis's, else its source when that is
    a web address. Never invented; None when there is none."""
    analysis = state.read_json(os.path.join("jobs", job["slug"], "analysis.json")) or {}
    for raw in (job.get("apply_url"), analysis.get("apply_url"), job.get("source")):
        if isinstance(raw, str) and re.match(r"https?://", raw.strip(), re.I):
            return raw.strip()
    return None


def _start(args):
    job = state.load_job(args.slug)
    status = job["status"]
    if status == "dismissed":
        raise Fail("this job was dismissed: restore it first", hint=f"`track restore {args.slug}`")
    if status in SENT:
        raise Fail(f"this job is already {state.LABELS[status]}", hint="`track timeline SLUG` shows what happened")
    path = cv_file(args.slug)
    if not path:
        raise Fail("there is no rendered CV for this job", hint=f"`cv render {args.slug}` writes cv.pdf (or cv.html)")
    url = apply_url(job)
    saved = answers.load()
    return {
        "job": {"slug": job["slug"], "company": job.get("company", ""), "role": job.get("role", ""), "status": status},
        "apply_url": url,
        "cv_file": path,
        "basics": state.load_profile()["cv"].get("basics") or {},
        "remembered_answers": [dict(row, key=key) for key, row in saved.items() if isinstance(row, dict)],
        "next": ("Fill the form from `basics`, `remembered_answers` and the user's own words; stop before submit and "
                 "show the user every answer. Submit only after their explicit yes, then `apply done SLUG --applied`."
                 if url else "There is no apply address for this job: ask the user for it. Never guess one."),
    }


def _done(args):
    job = state.load_job(args.slug)
    before = job["status"]
    if before == "dismissed":
        raise Fail("this job was dismissed: restore it first", hint=f"`track restore {args.slug}`")
    if args.applied:
        if before not in SENT:
            if not state.can_advance_to(before, "applied"):
                state.set_status(args.slug, "submitting", "sending again")
            state.set_status(args.slug, "applied", "application sent")
    else:
        state.set_status(args.slug, "failed", " ".join((args.reason or "").split()) or "the application could not be sent")
    after = state.load_job(args.slug)["status"]
    return {"slug": args.slug, "from": before, "status": after, "changed": before != after}


def register(groups):
    commands = group(groups, "apply", "Help with one application: what it needs, and how it ended.")
    start = command(commands, "start", _start, "What is needed to apply: address, CV file, basics, remembered answers.")
    start.add_argument("slug")
    done = command(commands, "done", _done, "Record how the application ended.")
    done.add_argument("slug")
    outcome = done.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--applied", action="store_true", help="the form was submitted")
    outcome.add_argument("--failed", action="store_true", help="it could not be sent")
    done.add_argument("--reason", default="")
