"""`track`: the board, a job's status and timeline."""
import datetime

from .. import state
from ..cli import Fail, command, group


def _days_since(stamp):
    try:
        then = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    return max(0, (datetime.datetime.now(datetime.timezone.utc) - then).days)


def card(job):
    """One job as the board shows it."""
    events = state.timeline(job["slug"])
    out = {
        "slug": job["slug"], "company": job.get("company", ""), "role": job.get("role", ""),
        "status": job["status"], "status_label": state.LABELS.get(job["status"], job["status"]),
        "last_event": events[-1] if events else None,
        "days_since_update": _days_since(job.get("updated_at") or job.get("created_at")),
    }
    if job["status"] == "interview" and job.get("interview_round"):
        out["interview_round"] = job["interview_round"]
    return out


def board(jobs):
    """All stages in order (empty ones too), most recently updated first. A dismissed job is on
    no stage."""
    jobs = sorted(jobs, key=lambda job: job.get("updated_at") or "", reverse=True)
    return [
        {"stage": stage, "jobs": [card(job) for job in jobs
                                  if job["status"] != "dismissed" and state.stage_for(job["status"]) == stage]}
        for stage in state.STAGES
    ]


def _brief(job):
    return {"slug": job["slug"], "company": job.get("company", ""), "role": job.get("role", ""),
            "status": job["status"], "status_label": state.LABELS.get(job["status"], job["status"])}


def _board(args):
    jobs = state.list_jobs()
    return {"stages": board(jobs), "dismissed": len([job for job in jobs if job["status"] == "dismissed"])}


def _status(args):
    job = state.load_job(args.slug)
    if args.new == "dismissed" or job["status"] == "dismissed":
        raise Fail("a job is dismissed and restored with its own commands",
                   hint="`track dismiss SLUG [--reason R]` / `track restore SLUG`")
    before = job["status"]
    job = state.set_status(args.slug, args.new, args.message or "", force=args.force)
    return {"job": _brief(job), "from": before, "changed": before != job["status"]}


def _note(args):
    state.load_job(args.slug)
    text = " ".join(args.text.split())
    if not text:
        raise Fail("the note is empty")
    state.add_event(args.slug, "note", text)
    return {"slug": args.slug, "noted": text}


def _timeline(args):
    job = state.load_job(args.slug)
    return {"job": _brief(job), "events": state.timeline(args.slug)}


def _interview(args):
    job = state.load_job(args.slug)
    if job["status"] != "interview":
        # Forward only: an offer or a rejection is not moved back to Interview.
        job = state.set_status(args.slug, "interview", "interview")
    current = job.get("interview_round") or 0
    wanted = args.round if args.round is not None else current + 1
    if wanted < 1:
        raise Fail("a round is 1 or more")
    if wanted != current:
        job["interview_round"] = wanted
        state.save_job(job)
        state.add_event(args.slug, "round", f"Round: {wanted}")
    return {"job": _brief(job), "interview_round": wanted}


def _dismiss(args):
    job = state.load_job(args.slug)
    before = job["status"]
    if before == "dismissed":
        return {"job": _brief(job), "changed": False}
    if not state.can_advance_to(before, "dismissed"):
        raise Fail(f"only a job in CVs can be dismissed ({args.slug} is {state.LABELS[before]})",
                   hint="a job that was sent is not dismissed: record the outcome instead (`track status`)")
    reason = " ".join((args.reason or "").split())
    job = state.set_status(args.slug, "dismissed", reason)
    job.update(dismissed_from=before, dismiss_reason=reason, dismissed_at=state.now())
    state.save_job(job)
    return {"job": _brief(job), "changed": True, "dismissed_from": before}


def _restore(args):
    job = state.load_job(args.slug)
    if job["status"] != "dismissed":
        raise Fail(f"{args.slug} is not dismissed")
    back = job.get("dismissed_from")
    if back not in state.STATUSES or back == "dismissed":
        back = "draft"
    job = state.set_status(args.slug, back, "restored", force=True)
    for key in ("dismissed_from", "dismiss_reason", "dismissed_at"):
        job.pop(key, None)
    state.save_job(job)
    return {"job": _brief(job), "restored_to": back}


def register(groups):
    commands = group(groups, "track", "The board, a job's status and timeline.")
    command(commands, "board", _board, "Jobs by stage: CVs | Applied | In progress | Done.")
    status = command(commands, "status", _status, "Move a job's status (forward only).")
    status.add_argument("slug")
    status.add_argument("new", choices=state.STATUSES)
    status.add_argument("--message", default="")
    status.add_argument("--force", action="store_true", help="only on the user's explicit correction")
    note = command(commands, "note", _note, "Add a note to a job's timeline.")
    note.add_argument("slug")
    note.add_argument("text")
    timeline = command(commands, "timeline", _timeline, "A job's events, oldest first.")
    timeline.add_argument("slug")
    interview = command(commands, "interview", _interview, "Record an interview (and its round).")
    interview.add_argument("slug")
    interview.add_argument("--round", type=int)
    dismiss = command(commands, "dismiss", _dismiss, "The user is no longer interested (CVs stage only).")
    dismiss.add_argument("slug")
    dismiss.add_argument("--reason", default="")
    restore = command(commands, "restore", _restore, "Bring a dismissed job back to where it was.")
    restore.add_argument("slug")
