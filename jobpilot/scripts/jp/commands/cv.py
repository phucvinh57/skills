"""`cv`: the tailored CV of a job. `check` runs the fact guard and saves only a clean CV; `render`
checks again and writes the PDF (or, without Typst, a print-ready HTML file)."""
import glob
import json
import os

from .. import cvmodel, render, state, typst
from ..cli import Fail, command, group, read_json_arg
from ..guard import guard
from . import docs

REPAIR = ("one repair round: fix every problem, remove or reword what the profile does not back, add no new claim, "
          "then run `cv check` again; if it still fails, stop and show the user the problems")


def _open_job(slug):
    job = state.load_job(slug)
    if job["status"] == "dismissed":
        raise Fail("this job was dismissed: restore it first")
    return job


def _profile():
    stored = state.load_profile()
    try:
        return stored, cvmodel.parse(stored["cv"])
    except cvmodel.CvError as err:
        raise Fail(f"profile.json is damaged: {err}", hint="read the profile again (references/profile.md)")


def validate(profile, raw):
    """`(cv, violations)`: a value that is not a CV is one violation at `cv`."""
    try:
        cv = cvmodel.parse(raw)
    except cvmodel.CvError as err:
        return None, [{"path": "cv", "message": str(err)}]
    return cv, guard(profile, cv)


def _strings(value, key):
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    if any(isinstance(item, (dict, list)) for item in items):
        raise Fail(f"report.{key}: expected a list of text")
    return [text for text in (" ".join(str(item).split()) for item in items) if text]


def _file(slug, name):
    return os.path.join("jobs", slug, name)


def _check(args):
    job = _open_job(args.slug)
    slug = job["slug"]
    stored, profile = _profile()
    raw = read_json_arg(args.file)
    report = None
    if args.report:
        given = read_json_arg(args.report)
        if not isinstance(given, dict):
            raise Fail("the report must be a JSON object", hint="see references/schemas.md")
        report = {key: _strings(given.get(key), key) for key in ("emphasised", "omitted", "gaps")}

    cv, violations = validate(profile, raw)
    if violations:
        rejected = state.write_json(_file(slug, "cv.rejected.json"), raw)
        raise Fail(f"the CV breaks {len(violations)} fact rule(s); it was not saved", hint=REPAIR,
                   violations=violations, rejected=rejected)

    first = state.read_json(_file(slug, "cv.json")) is None
    saved = state.write_json(_file(slug, "cv.json"), cvmodel.compact(cv))
    try:
        os.unlink(state.path(_file(slug, "cv.rejected.json")))
    except OSError:
        pass
    if report is not None:
        state.write_json(_file(slug, "report.json"), report)
        if first or report["gaps"]:
            job["gaps"] = report["gaps"]
            state.save_job(job)
        for label, key in (("Emphasised", "emphasised"), ("Omitted", "omitted"), ("Gaps", "gaps")):
            if report[key]:
                state.add_event(slug, "cv", f"{label}: {'; '.join(report[key])}")
    out = {"ok": True, "saved": saved, "counts": cvmodel.counts(cv), "next": f"`cv render {slug}`"}
    if stored.get("documents_hash") != docs.documents_hash():
        out["warning"] = "the documents changed since the profile was read; the CV was checked against the stored profile"
    return out


def _render(args):
    job = _open_job(args.slug)
    slug = job["slug"]
    if args.max_pages < 1:
        raise Fail("--max-pages must be at least 1")
    raw = state.read_json(_file(slug, "cv.json"))
    if raw is None:
        raise Fail("there is no checked CV for this job yet", hint=f"`cv check {slug} --file cv.json`")
    _, profile = _profile()
    cv, violations = validate(profile, raw)
    if violations:
        raise Fail(f"the saved CV breaks {len(violations)} fact rule(s); it is not rendered", hint=REPAIR,
                   violations=violations)
    try:
        out = render.render_job(slug, cv)
    except typst.TypstError as err:
        raise Fail("Typst could not render the CV", hint=REPAIR, violations=[{"path": "typst", "message": err.message}])
    except OSError as err:
        raise Fail(f"the CV could not be written: {err}")

    if "html" in out:
        out["note"] = "Typst is not installed, so this is an HTML file: " + typst.INSTALL_HINT
    elif out["page_count"] is not None and out["page_count"] > args.max_pages:
        out["too_long"] = True
        out["message"] = (f"the CV takes {out['page_count']} pages (target {args.max_pages}): one trim round, "
                          "dropping the least relevant bullets and rewording nothing")

    cv_hash = state.text_hash(json.dumps(raw, sort_keys=True, ensure_ascii=False))
    previous = job.get("cv_hash")
    if job["status"] in ("draft", "failed"):
        job = state.set_status(slug, "cv_ready", "CV ready")
    if previous != cv_hash:
        job["cv_hash"] = cv_hash
        state.save_job(job)
        state.add_event(slug, "cv", "CV revised" if previous else "CV generated")
    if out.get("too_long"):
        state.add_event(slug, "warning", f"the CV takes {out['page_count']} pages (target {args.max_pages})")
    out["status"] = job["status"]
    return out


def _show(args):
    job = state.load_job(args.slug)
    slug = job["slug"]
    cv = state.read_json(_file(slug, "cv.json"))
    if cv is None:
        raise Fail("there is no checked CV for this job yet", hint=f"`cv check {slug} --file cv.json`")
    folder = state.job_dir(slug)
    out = {"cv": cv, "file": os.path.join(folder, "cv.json"), "status": job["status"], "gaps": job.get("gaps", [])}
    rendered = job.get("cv_hash") == state.text_hash(json.dumps(cv, sort_keys=True, ensure_ascii=False))
    for key, name in (("pdf", "cv.pdf"), ("html", "cv.html")):
        if os.path.isfile(os.path.join(folder, name)):
            out[key] = os.path.join(folder, name)
    if "pdf" in out:
        out["page_count"] = len(glob.glob(os.path.join(folder, "page-*.png")))
    if ("pdf" in out or "html" in out) and not rendered:
        out["note"] = f"the CV changed since it was rendered: `cv render {slug}`"
    return out


def register(groups):
    commands = group(groups, "cv", "The tailored CV of a job: fact check, render, show.")
    check = command(commands, "check", _check, "Check a CV against the profile; save it only when it is clean.")
    check.add_argument("slug")
    check.add_argument("--file", required=True, help="the CV as JSON (`-` for stdin)")
    check.add_argument("--report", help="JSON with `emphasised`, `omitted`, `gaps`")
    make = command(commands, "render", _render, "Render the checked CV to PDF (HTML without Typst).")
    make.add_argument("slug")
    make.add_argument("--max-pages", type=int, default=1)
    show = command(commands, "show", _show, "The saved CV and its rendered files.")
    show.add_argument("slug")
