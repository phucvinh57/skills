"""Job sources: code that turns the preferences into candidate postings. The model never picks
URLs; every candidate comes from an adapter reading a public API or page.

| kind | address | how |
|------|---------|-----|
| `greenhouse` | board URL or name (`boards.greenhouse.io/acme`) | public board JSON |
| `lever` | `jobs.lever.co/acme` or name | public postings JSON |
| `ashby` | `jobs.ashbyhq.com/acme` or name | public job-board JSON |
| `smartrecruiters` | `jobs.smartrecruiters.com/Acme` or company id | public postings JSON, one API page (100) at a time |
| `recruitee` | `acme.recruitee.com` or name | public offers JSON |
| `personio` | `acme.jobs.personio.de` (or `.com`) or name | public XML job feed |
| `workday` | `https://acme.wd5.myworkdayjobs.com/[en-US/]External` | the board page's own job search (POST), one query per wanted role |
| `hn_hiring` | unused | HN Algolia, the latest "Who is hiring?" thread, one comment per candidate |
| `yc` | `https://www.ycombinator.com/jobs` (default) or a workatastartup.com page | the job links of the listing page |
| `career_page` | any page that lists jobs | its `JobPosting` JSON-LD, else the same-site links that look like postings, else the site's `sitemap.xml` |

There is no browser here. A page that needs one (a login or bot wall, a list JavaScript draws) is
`NeedsBrowser`: the agent may read it with its own browsing tool and add the job by hand; a login,
captcha or block page is never worked around.

    adapter = adapter_for(check_source(kind, address))
    page = adapter.search(prefs, cursor=None, page_size=25)   # {"candidates": [...], "next": cursor or None}
    text = adapter.posting(candidate) or fetch_posting(candidate)
"""
from ..cli import Fail
from . import boards, hn_hiring, personio, recruitee, web, workday, yc
from .ashby import Ashby
from .base import Adapter, NeedsBrowser, board_host, board_slug, mentions, title_fits_roles, within
from .career_page import CareerPage
from .greenhouse import Greenhouse
from .hn_hiring import HnHiring
from .lever import Lever
from .personio import Personio
from .recruitee import Recruitee
from .smartrecruiters import SmartRecruiters
from .web import fetch_posting
from .workday import Workday
from .yc import Yc

KINDS = ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "personio", "workday", "hn_hiring", "yc", "career_page")
LABELS = dict(boards.LABELS)

# What to enter as the address of a kind (a hint, not a rule).
ADDRESS_HINTS = {
    "greenhouse": "board URL or name, e.g. boards.greenhouse.io/acme",
    "lever": "jobs.lever.co/acme or acme",
    "ashby": "jobs.ashbyhq.com/acme or acme",
    "smartrecruiters": "jobs.smartrecruiters.com/Acme or Acme",
    "recruitee": "acme.recruitee.com or acme",
    "personio": "acme.jobs.personio.de (or .com) or acme",
    "workday": "board URL, e.g. https://acme.wd5.myworkdayjobs.com/External",
    "hn_hiring": "no address needed",
    "yc": "optional: https://www.ycombinator.com/jobs",
    "career_page": "any page that lists jobs, e.g. https://acme.com/careers",
}

# kind -> (what it is called in an error, the domains a board URL may be on)
_SLUG_BOARDS = {
    "greenhouse": ("Greenhouse source", ("greenhouse.io",)),
    "lever": ("Lever source", ("lever.co",)),
    "ashby": ("Ashby source", ("ashbyhq.com",)),
    "smartrecruiters": ("SmartRecruiters source", ("smartrecruiters.com",)),
}
_HOST_BOARDS = {
    "recruitee": ("Recruitee source", recruitee),
    "personio": ("Personio source", personio),
}


def _unknown(kind):
    return Fail(f"no adapter for source kind `{kind}` yet", hint="the kinds are: " + ", ".join(KINDS))


def _check(kind, url):
    url = (url or "").strip()
    if kind in _SLUG_BOARDS:
        what, hosts = _SLUG_BOARDS[kind]
        return kind, url, within(what, board_slug, url, hosts)
    if kind in _HOST_BOARDS:
        what, module = _HOST_BOARDS[kind]
        return kind, url, within(what, board_host, url, module.HOSTS)[0]
    if kind == "hn_hiring":
        return kind, "", "Hacker News: Who is hiring"
    if kind == "workday":
        return kind, url, within("Workday source", workday.Board.parse, url).tenant
    if kind == "yc":
        url = url or yc.DEFAULT_URL
        yc.check_url(url)
        return kind, url, "Y Combinator jobs"
    if kind == "career_page":
        parsed = within("job page source", web.normalize_page_url, url)
        # `hint_for` only names a board whose address passes the board kind's own check.
        board = boards.hint_for(parsed)
        if board is not None:
            return _check(board["kind"], board["url"])
        within("job page source", web.check_site, parsed)
        host = parsed.host
        while host.startswith("www."):
            host = host[4:]
        return kind, url, host
    raise _unknown(kind)


def check_source(kind, url):
    """Check a new source's address before it is stored, without reading anything:
    `{"kind", "url", "name"}`. `kind` is the one asked for, or the board's when a job page's address
    is a company board (`boards.greenhouse.io/acme` given as a job page is a `greenhouse` source);
    `url` the address to store (an empty one becomes the kind's default where there is one); `name`
    a name for it (the board name, the host or the kind's title). `Fail` says why an address is not
    one the kind reads."""
    try:
        kind, url, name = _check(kind, url)
    except Fail as err:
        if not err.hint and kind in ADDRESS_HINTS:
            err.hint = f"expected address: {ADDRESS_HINTS[kind]}"
        raise
    return {"kind": kind, "url": url, "name": name}


def adapter_for(source, http=None):
    """The adapter that reads `source` (`{"kind", "url", "name"}`). `Fail` for a kind without one
    and for an address the adapter cannot read."""
    kind, url = source.get("kind", ""), source.get("url") or ""
    name = (source.get("name") or "").strip()
    if kind in _SLUG_BOARDS:
        what, hosts = _SLUG_BOARDS[kind]
        slug = within(what, board_slug, url, hosts)
        adapter = {"greenhouse": Greenhouse, "lever": Lever, "ashby": Ashby, "smartrecruiters": SmartRecruiters}[kind]
        return adapter(slug, name or slug, http)
    if kind in _HOST_BOARDS:
        what, module = _HOST_BOARDS[kind]
        company, suffix = within(what, board_host, url, module.HOSTS)
        adapter = Recruitee if kind == "recruitee" else Personio
        return adapter(module.ORIGIN or f"https://{company}.{suffix}", name or company, http)
    if kind == "hn_hiring":
        return HnHiring(http)
    if kind == "workday":
        board = within("Workday source", workday.Board.parse, url)
        return Workday(board, name or board.tenant, http)
    if kind == "yc":
        return Yc(source, http)
    if kind == "career_page":
        # The company a job page's postings belong to when the page does not say: the source's
        # name, else its host.
        return CareerPage(source, name or within("job page source", web.normalize_page_url, url).host, http)
    raise _unknown(kind)


__all__ = [
    "KINDS", "LABELS", "ADDRESS_HINTS", "Adapter", "NeedsBrowser", "adapter_for", "check_source", "fetch_posting",
    "mentions", "title_fits_roles",
]
