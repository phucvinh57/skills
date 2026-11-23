"""`brief`: the company brief of a lead or a job: the pages to read, then the grounded brief."""
import os

from .. import insight, leads, state
from ..cli import Fail, command, group, read_json_arg

CACHE = "brief_pages.json"


def _target(args):
    """`(kind, id, folder, posting text, posting url)` of the lead or job the command names."""
    if (args.lead is None) == (args.job is None):
        raise Fail("name one: --lead ID or --job SLUG")
    if args.lead is not None:
        lead = leads.get(args.lead)
        url = lead.get("url", "")
        return "lead", lead["id"], os.path.join("leads", str(lead["id"])), leads.posting_text(lead), url
    job = state.load_job(args.job)
    folder = os.path.join("jobs", job["slug"])
    text = leads.strip_jd_header(state.read_text(os.path.join(folder, "jd.md")) or "")
    # Never the apply link: only the address the posting came from tells the company's site.
    return "job", job["slug"], folder, text, job.get("source", "")


def _web(url):
    return url if (url or "").lower().startswith(("http://", "https://")) else None


def _pages(args):
    kind, ident, folder, text, url = _target(args)
    if not text.strip():
        raise Fail("there is no posting text to research")
    urls = insight.company_urls(_web(url))
    pages = insight.fetch_pages(urls)
    state.write_json(os.path.join(folder, CACHE), {"posting_url": _web(url), "pages": pages, "fetched_at": state.now()})
    parts = [state.fence("jd", text[:insight.MAX_JD_CHARS])]
    if not pages:
        parts.append("No page of the company's website could be read.")
    for number, page in enumerate(pages, 1):
        parts.append(state.fence("page", page["text"], n=number, url=page["url"]))
    flag = f"--{kind} {ident}"
    return {
        "for": {kind: ident},
        "posting_url": _web(url),
        "tried": urls,
        "pages": [{"n": number, "url": page["url"]} for number, page in enumerate(pages, 1)],
        "note": state.UNTRUSTED_NOTE,
        "text": "\n\n".join(parts),
        "next": f"write the brief as JSON (references/discover.md), then: jobpilot.py brief check {flag} --file brief.json",
    }


def _known_skills(folder):
    """The skill lists of the job's analysis, when there is one: the brief uses them as they are."""
    analysis = state.read_json(os.path.join(folder, "analysis.json"))
    if not isinstance(analysis, dict):
        return None
    required, preferred = analysis.get("must_have"), analysis.get("nice_to_have")
    lists = [value if isinstance(value, list) else [] for value in (required, preferred)]
    lists = [[item for item in value if isinstance(item, str)] for value in lists]
    return tuple(lists) if any(lists) else None


def _check(args):
    kind, ident, folder, text, url = _target(args)
    cached = state.read_json(os.path.join(folder, CACHE))
    flag = f"--{kind} {ident}"
    if not isinstance(cached, dict):
        raise Fail("the company pages were not read yet", hint=f"first: jobpilot.py brief pages {flag}")
    brief, dropped = insight.assemble(read_json_arg(args.file), text, cached.get("pages") or [], _web(url),
                                      _known_skills(folder) if kind == "job" else None)
    if kind == "lead":
        rows = leads.load()
        leads.find(rows, ident)["insight"] = brief
        leads.save(rows)
    else:
        job = state.load_job(ident)
        job["insight"] = brief
        state.save_job(job)
    out = {"for": {kind: ident}, "insight": brief, "dropped": dropped, "saved": True}
    if dropped:
        out["note"] = "what could not be found in the posting or the pages was left out; do not add it back"
    return out


def register(groups):
    commands = group(groups, "brief", "The company brief of a lead or a job, grounded in the texts that were read.")
    pages = command(commands, "pages", _pages, "Read the company's own pages and print them with the posting.")
    check = command(commands, "check", _check, "Check a brief against the texts and save what is grounded.")
    for parser in (pages, check):
        parser.add_argument("--lead", type=int, help="a lead's id")
        parser.add_argument("--job", help="a job's slug")
    check.add_argument("--file", required=True, help="the brief as JSON, or - for stdin")
