"""`track`: the board, a job's status and timeline, and what a reply email says."""
import datetime

from .. import mailmatch, state
from ..cli import Fail, command, group, read_json_arg


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


def _email_candidates(args):
    sender = mailmatch.full_from(args.sender, args.name or "")
    if not mailmatch.sender_host(sender):
        raise Fail("--from needs the sender's address (name@host)")
    found, how = mailmatch.candidates(sender, args.subject, args.snippet or "")
    out = {
        "candidates": [dict(_brief(job), why=why) for job, why in found],
        "matched_by": how,
        "about_a_job": bool(found),
    }
    if how == "matched":
        out["next"] = "Classify the email among these candidates (references/track.md), then `track email --file F`."
    elif how == "job_hints":
        out["next"] = ("No job matches the sender; the text reads like a job email. Classify among these candidates;"
                       " `job` is null unless the email clearly names one of them.")
    else:
        out["next"] = "Not about one of the user's jobs: skip this email."
    return out


def _email(args):
    raw = read_json_arg(args.file)
    if not isinstance(raw, dict):
        raise Fail("the classification must be one JSON object",
                   hint='{"id", "job", "kind", "confidence", "summary", "from", "subject", "received_at"}')
    return mailmatch.report(raw, confirmed=args.confirmed)


def _email_dismiss(args):
    return {"email": mailmatch.dismiss(args.id)}


def _emails(args):
    stored = mailmatch.emails()
    if args.review:
        stored = [email for email in stored if mailmatch.needs_review(email)]
    return {"emails": stored}


def register(groups):
    commands = group(groups, "track", "The board, a job's status and timeline, reply emails.")
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
    candidates = command(commands, "email-candidates", _email_candidates, "The jobs an email can be about.")
    candidates.add_argument("--from", dest="sender", required=True, help="the sender's address, or `Name <address>`")
    candidates.add_argument("--subject", required=True)
    candidates.add_argument("--name", help="the sender's display name")
    candidates.add_argument("--snippet", help="the first lines of the email")
    email = command(commands, "email", _email, "Record a classified email.")
    email.add_argument("--file", required=True, help="the classification as JSON (`-` = stdin)")
    email.add_argument("--confirmed", action="store_true", help="the user confirmed this reading")
    wrong = command(commands, "email-dismiss", _email_dismiss, "The user says a low-confidence reading is wrong.")
    wrong.add_argument("--id", required=True)
    listed = command(commands, "emails", _emails, "The recorded emails.")
    listed.add_argument("--review", action="store_true", help="only those waiting for the user's confirmation")
