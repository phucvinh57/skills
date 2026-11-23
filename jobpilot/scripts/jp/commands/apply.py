"""`apply`: help with one application form. The script never opens a page and never submits:
the agent (with a browser tool) or the user does, and only after the user's explicit yes."""
import os

from .. import answers, applyhelp, state
from ..cli import Fail, command, group, read_json_arg, read_text_arg

SENT = ("applied", "interview", "rejected", "offer")


def _review_file(slug):
    return os.path.join("jobs", slug, "application.json")


def _open_job(slug):
    """A job an application can be worked on: its CV is ready and it was not sent yet."""
    job = state.load_job(slug)
    status = job["status"]
    if status == "dismissed":
        raise Fail("this job was dismissed: restore it first", hint=f"`track restore {slug}`")
    if status in SENT:
        raise Fail(f"this job is already {state.LABELS[status]}", hint="`track timeline SLUG` shows what happened")
    if status == "draft":
        raise Fail("this job has no CV yet", hint="tailor and render the CV first (references/tailor.md)")
    return job


def _start(args):
    job = _open_job(args.slug)
    cv_path = applyhelp.cv_file(args.slug)
    if not cv_path:
        raise Fail("there is no rendered CV for this job", hint=f"`cv render {args.slug}` writes cv.pdf (or cv.html)")
    profile = state.load_profile()
    url = applyhelp.apply_url(job)
    ats = applyhelp.detect_ats(url) if url else "generic"
    handoff = ats in applyhelp.HANDOFF_ONLY
    out = {
        "job": {"slug": job["slug"], "company": job.get("company", ""), "role": job.get("role", ""), "status": job["status"]},
        "apply_url": url,
        "form_url": applyhelp.form_url(ats, url) if url else None,
        "cv_file": cv_path,
        "basics": profile["cv"].get("basics") or {},
        "ats": ats,
        "ats_label": applyhelp.ATS_LABELS[ats],
        "needs_user_session": ats in applyhelp.NEEDS_USER_SESSION,
        "handoff_only": handoff,
        "safety_rules": list(applyhelp.SAFETY_RULES),
    }
    if not url:
        out["next"] = "There is no apply address for this job: ask the user for it. Never guess one."
    elif handoff:
        out["next"] = (f"{applyhelp.ATS_LABELS[ats]} shows its form only to the signed-in user: hand over. Give the user"
                       " the address and the CV file, help with the questions they paste (`apply answers`), and never"
                       " sign in for them.")
    else:
        out["next"] = "Read the form's fields (references/apply.md), then `apply answers SLUG --fields F`."
    return out


def _answers(args):
    job = _open_job(args.slug)
    return applyhelp.plan(job, applyhelp.read_fields(read_json_arg(args.fields)))


def _check(args):
    job = _open_job(args.slug)
    fields = applyhelp.read_fields(read_json_arg(args.fields))
    review = applyhelp.review(job, fields, read_json_arg(args.file))
    state.write_json(_review_file(args.slug), review)
    if review["ready"]:
        if job["status"] != "submitting":
            # The review waits for the user's yes; `apply done --discard` takes it back.
            state.set_status(args.slug, "submitting", "review ready, waiting for your yes")
        review["next"] = ("Show the user every row (label, value, source, warnings) and the attachment. Submit only"
                          " after their explicit yes for this job; then `apply done SLUG --applied`.")
    else:
        review["next"] = ("Not ready: ask the user about `missing` and `unconfirmed_personal` (answer with source"
                          " \"user\"), fix `invalid`, then run `apply check` again.")
    review["status"] = state.load_job(args.slug)["status"]
    return review


def _reviewed(slug):
    review = state.read_json(_review_file(slug))
    if not review or not review.get("rows"):
        raise Fail("there are no reviewed answers to remember", hint="`apply check` writes them; nothing was changed")
    return review


def _remember(slug, only):
    review = _reviewed(slug)
    remembered, skipped = [], []
    for row in review["rows"]:
        if only and row["id"] not in only:
            continue
        # Without ids: what the user or the page said. Text composed for this one job is
        # remembered only when the user picked it by id.
        if applyhelp.rememberable(row) and (only or row.get("source") != "generated"):
            if answers.save(row["label"], row["value"]):
                remembered.append({"question": row["label"].strip(), "answer": row["value"].strip()})
                continue
        if only:
            skipped.append(row["id"])
    if remembered:
        state.add_event(slug, "apply", f"remembered {len(remembered)} answer(s)")
    return remembered, skipped


def _done(args):
    job = state.load_job(args.slug)
    before = job["status"]
    if before == "dismissed":
        raise Fail("this job was dismissed: restore it first", hint=f"`track restore {args.slug}`")
    if args.remember is not None:
        _reviewed(args.slug)
    if args.applied:
        if before not in SENT:
            if not state.can_advance_to(before, "applied"):
                state.set_status(args.slug, "submitting", "sending again")
            state.set_status(args.slug, "applied", "application sent")
    elif args.failed:
        state.set_status(args.slug, "failed", " ".join((args.reason or "").split()) or "the application could not be sent")
    elif before == "submitting":
        state.set_status(args.slug, "cv_ready", "review discarded", force=True)
    out = {"slug": args.slug, "from": before, "status": state.load_job(args.slug)["status"]}
    out["changed"] = out["from"] != out["status"]
    if args.remember is not None:
        out["remembered"], skipped = _remember(args.slug, set(args.remember))
        if skipped:
            out["not_remembered"] = skipped
    return out


def _page(args):
    text = read_text_arg(args) or ""
    blocked = applyhelp.captcha_text(text)
    out = {"blocked": bool(blocked), "looks_submitted": not blocked and applyhelp.success_text(text)}
    if blocked:
        out["reason"] = blocked
        out["next"] = "Stop. Do not try to pass it. Tell the user and let them deal with it in their own browser."
    return out


def register(groups):
    commands = group(groups, "apply", "Help with an application form: answers, checks, the outcome.")
    start = command(commands, "start", _start, "What is needed to apply: address, CV file, basics, the rules.")
    start.add_argument("slug")
    plan = command(commands, "answers", _answers, "What code settles for each form field, and what is left.")
    plan.add_argument("slug")
    plan.add_argument("--fields", required=True, help="the form's fields as JSON (`-` = stdin)")
    check = command(commands, "check", _check, "Check the answers against the fields and save the review.")
    check.add_argument("slug")
    check.add_argument("--fields", required=True)
    check.add_argument("--file", required=True, help="{field_id: value} as JSON")
    done = command(commands, "done", _done, "Record how the application ended.")
    done.add_argument("slug")
    outcome = done.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--applied", action="store_true", help="the form was submitted")
    outcome.add_argument("--failed", action="store_true", help="it could not be sent")
    outcome.add_argument("--discard", action="store_true", help="drop the review without sending")
    done.add_argument("--reason", default="")
    done.add_argument("--remember", nargs="*", metavar="FIELD_ID",
                      help="remember the reviewed answers (all the user gave, or only these field ids)")
    page = command(commands, "page", _page, "Does a page's text show a human check, or a sent application?")
    page.add_argument("--text")
    page.add_argument("--file", help="the page's visible text (`-` = stdin)")
