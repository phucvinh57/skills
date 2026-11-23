"""`job`: one posting -> one job (an application). `read` fetches the posting text, the agent
writes its analysis, `add` creates the job from both."""
import os
import re
import urllib.parse

from .. import dedup, ingest, state
from ..cli import Fail, command, group, read_json_arg

MAX_JD_CHARS = 24_000
SENIORITY = ("intern", "junior", "mid", "senior", "staff", "lead", "manager")
_JD_ID = re.compile(r"^[0-9a-f]{16}$")


def valid_http_url(candidate):
    """The URL when it is absolute http(s) with a host; anything else (the analysis is written
    from untrusted text) is dropped."""
    candidate = (candidate or "").strip()
    try:
        parts = urllib.parse.urlsplit(candidate)
        host = parts.hostname
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host or re.search(r"\s", candidate):
        return ""
    return candidate


def base_slug(analysis):
    """The slug before uniqueness: the analysis's, else `company-role`."""
    given = state.slugify(analysis["slug"])
    return given if given != "job" else state.slugify(f"{analysis['company']} {analysis['role']}")


def _text(value, key):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        raise Fail(f"analysis.{key}: expected text")
    return " ".join(str(value).split())


def _texts(value, key):
    if value is None:
        return []
    if isinstance(value, dict):
        raise Fail(f"analysis.{key}: expected a list of text")
    items = value if isinstance(value, list) else [value]
    return [text for text in (_text(item, key) for item in items) if text]


def clean_analysis(raw):
    """The agent's analysis, validated: every key present, text trimmed, nothing but a real
    http(s) link kept as `apply_url`, a known seniority or none."""
    if not isinstance(raw, dict):
        raise Fail("the analysis must be a JSON object", hint="see references/schemas.md")
    analysis = {key: _text(raw.get(key), key) for key in ("company", "role", "slug", "location")}
    analysis["apply_url"] = valid_http_url(_text(raw.get("apply_url"), "apply_url"))
    for key in ("must_have", "nice_to_have", "key_terms"):
        analysis[key] = _texts(raw.get(key), key)
    seniority = _text(raw.get("seniority"), "seniority").lower()
    analysis["seniority"] = seniority if seniority in SENIORITY else ""
    if not analysis["role"]:
        raise Fail("analysis.role is empty", hint="the job title as the posting writes it")
    return analysis


def _read(args):
    given = [name for name in ("url", "file", "text") if getattr(args, name)]
    if len(given) != 1:
        raise Fail("give one of --url, --file or --text")
    source = ""
    if args.url:
        text, response = ingest.read_page(args.url)
        source = dedup.canonical_url(getattr(response, "url", "") or args.url) or dedup.canonical_url(args.url)
    elif args.file:
        text = ingest.read_file(args.file)
    else:
        text = ingest.html_to_text(args.text) if ingest.looks_like_html(args.text) else ingest.clean(args.text)
    if args.source and not source:
        source = dedup.canonical_url(args.source)
    if len(text.strip()) < ingest.MIN_CHARS:
        raise Fail("the posting has no readable text",
                   hint="read the page with your own browsing tool, or ask the user to paste the posting, then pass it with --file")
    text, truncated = ingest.truncate(text, MAX_JD_CHARS)
    jd = state.text_hash(source + "\n" + text)[:16]
    state.write_text(os.path.join("inbox", jd + ".md"), text)
    state.write_json(os.path.join("inbox", jd + ".json"), {"source": source, "truncated": truncated, "read_at": state.now()})
    out = {"jd": jd, "source": source, "chars": len(text), "truncated": truncated, "note": state.UNTRUSTED_NOTE,
           "text": state.fence("jd", text.rstrip("\n")),
           "next": f"write the analysis (references/tailor.md), then `job add --jd {jd} --file analysis.json`"}
    known = dedup.find_known(url=source) if source else None
    if known:
        out["known"] = known
    return out


def _add(args):
    if not _JD_ID.match(args.jd):
        raise Fail(f"not a posting id: {args.jd!r}", hint="`job read` prints it as `jd`")
    text = state.read_text(os.path.join("inbox", args.jd + ".md"))
    if text is None:
        raise Fail(f"no posting {args.jd!r} was read", hint="run `job read` first")
    source = (state.read_json(os.path.join("inbox", args.jd + ".json"), {}) or {}).get("source", "")
    analysis = clean_analysis(read_json_arg(args.file))

    known = dedup.find_known(url=source, company=analysis["company"], role=analysis["role"])
    if not known and analysis["apply_url"]:
        known = dedup.find_known(url=analysis["apply_url"])
    if known and known["kind"] == "job":
        raise Fail("this posting is already a job", hint=f"`job show {known['id']}`", job=state.load_job(known["id"]))

    job = state.new_job(analysis["company"], analysis["role"], source=source, apply_url=analysis["apply_url"],
                        slug=base_slug(analysis), lead_id=known["id"] if known else None)
    slug = job["slug"]
    title = f"{analysis['company']} — {analysis['role']}" if analysis["company"] else analysis["role"]
    state.write_text(os.path.join("jobs", slug, "jd.md"), f"# {title}\nSource: {source or 'pasted text'}\n\n{text}")
    state.write_json(os.path.join("jobs", slug, "analysis.json"), analysis)
    out = {"job": job, "analysis": analysis, "dir": state.job_dir(slug),
           "next": f"write the CV (references/tailor.md), then `cv check {slug} --file cv.json --report report.json`"}
    if known:
        # The user asked for a CV for a job discovery had already found: it leaves the queue.
        rows = state.read_json("leads.json", [])
        for lead in rows:
            if lead.get("id") == known["id"] and lead.get("state") != "promoted":
                lead.update(state="promoted", job_slug=slug, filtered_reason=None)
                state.write_json("leads.json", rows)
        out["lead"] = known
    return out


def _analyze(args):
    """The analysis of a job that already exists (a promoted lead has a posting but no analysis yet).
    Company and role stay as the job has them."""
    job = state.load_job(args.slug)
    if jd_body(args.slug) is None:
        raise Fail("this job has no posting text", hint="read the posting with `job read` and create the job with `job add`")
    analysis = clean_analysis(read_json_arg(args.file))
    if analysis["apply_url"] and not job.get("apply_url"):
        job["apply_url"] = analysis["apply_url"]
        state.save_job(job)
    state.write_json(os.path.join("jobs", args.slug, "analysis.json"), analysis)
    return {"job": job, "analysis": analysis,
            "next": f"write the CV (references/tailor.md), then `cv check {args.slug} --file cv.json --report report.json`"}


def _list(args):
    return {"jobs": [
        {"slug": job["slug"], "company": job.get("company", ""), "role": job.get("role", ""), "status": job["status"],
         "stage": state.stage_for(job["status"]), "updated_at": job.get("updated_at")}
        for job in state.list_jobs()
    ]}


def jd_body(slug):
    """The posting text of a job, without the header `job add` wrote."""
    text = state.read_text(os.path.join("jobs", slug, "jd.md"), "")
    head, separator, body = text.partition("\n\n")
    return body if separator and head.startswith("# ") else text


def _show(args):
    job = state.load_job(args.slug)
    folder = state.job_dir(args.slug)
    files = {}
    for name in sorted(os.listdir(folder)):
        if name.startswith(".") or name in ("job.json", "timeline.json", "cv.typ", "cv.render.json"):
            continue
        files[name] = os.path.join(folder, name)
    out = {
        "job": job, "stage": state.stage_for(job["status"]),
        "analysis": state.read_json(os.path.join("jobs", args.slug, "analysis.json")),
        "gaps": job.get("gaps", []), "timeline": state.timeline(args.slug), "files": files,
    }
    if args.jd:
        out["note"] = state.UNTRUSTED_NOTE
        out["jd"] = state.fence("jd", jd_body(args.slug).rstrip("\n"))
    return out


def register(groups):
    commands = group(groups, "job", "Jobs: read a posting, create the job, list and show jobs.")
    read = command(commands, "read", _read, "Read a posting (link, file or pasted text) and print it between markers.")
    read.add_argument("--url")
    read.add_argument("--file")
    read.add_argument("--text")
    read.add_argument("--source", help="the posting's address, when its text is passed with --file or --text")
    add = command(commands, "add", _add, "Create a job from a posting that was read and its analysis.")
    add.add_argument("--jd", required=True, help="the id `job read` printed")
    add.add_argument("--file", required=True, help="the analysis as JSON (`-` for stdin)")
    analyze = command(commands, "analyze", _analyze, "Save the analysis of a job that already exists (a promoted lead).")
    analyze.add_argument("slug")
    analyze.add_argument("--file", required=True, help="the analysis as JSON (`-` for stdin)")
    command(commands, "list", _list, "Every job with its status and stage.")
    show = command(commands, "show", _show, "One job: analysis, gaps, timeline, files.")
    show.add_argument("slug")
    show.add_argument("--jd", action="store_true", help="also print the posting between markers")
