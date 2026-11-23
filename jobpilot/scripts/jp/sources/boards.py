"""Spotting the hiring system behind a careers page: a page that embeds a Greenhouse board, links
to Lever postings or loads a Workday widget lists its jobs there, and the board's own source reads
them better than the page does.

What a page names is **only a suggestion**: nothing reads a detected board by itself. A hint is
shown to the user, who may add it as a source. Every hint is a board address built by code from the
pattern table below and checked by `check_source`, the same check as a source the user types, so a
bad name is never suggested.

| Address | Kind |
|---------|------|
| `boards.greenhouse.io/<slug>`, `job-boards.greenhouse.io/<slug>`, `boards.greenhouse.io/embed/job_board[/js]?for=<slug>` | `greenhouse` |
| `jobs.lever.co/<slug>` | `lever` |
| `jobs.ashbyhq.com/<slug>` | `ashby` |
| `jobs.smartrecruiters.com/<Company>` | `smartrecruiters` |
| `<company>.recruitee.com` | `recruitee` |
| `<company>.jobs.personio.de` / `.com` | `personio` |
| `<tenant>.wd<N>.myworkdayjobs.com/[<locale>/]<site>` | `workday` |
"""
import re

from ..cli import Fail
from . import personio, recruitee, workday
from .base import board_host, is_board_name, parse_url
from .web import resolve

LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "smartrecruiters": "SmartRecruiters",
    "recruitee": "Recruitee", "personio": "Personio", "workday": "Workday", "hn_hiring": "HN Who is hiring",
    "yc": "Y Combinator", "career_page": "Job page",
}

# The registrable domains of each board kind.
BOARD_DOMAINS = (
    ("greenhouse", ("greenhouse.io",)),
    ("lever", ("lever.co",)),
    ("ashby", ("ashbyhq.com",)),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("recruitee", ("recruitee.com",)),
    ("personio", ("personio.de", "personio.com")),
    ("workday", ("myworkdayjobs.com",)),
)
_SLUG_HOSTS = {"lever": "jobs.lever.co", "ashby": "jobs.ashbyhq.com", "smartrecruiters": "jobs.smartrecruiters.com"}
# Most hints taken from one page.
MAX_HINTS = 10
# Labels on a board's domain that are the vendor's own pages, not a company's board.
VENDOR_LABELS = ("www", "app", "api", "help", "blog", "support", "status", "docs")

_TAG = re.compile(r"""<(a|iframe|script)\b[^>]*?\s(href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I | re.S)


def kind_for_host(host):
    """The board kind whose domain `host` is on (`boards.greenhouse.io` -> `greenhouse`); None for
    any other host, look-alikes (`greenhouse.io.evil.test`) included."""
    host = (host or "").strip().rstrip(".").lower()
    for kind, domains in BOARD_DOMAINS:
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return kind
    return None


def _hint(kind, address):
    return {"kind": kind, "url": address, "label": LABELS.get(kind, kind)}


def detect_boards(html, base):
    """The boards that `html` (read at `base`, a `Url`) points to, through `<a href>`,
    `<iframe src>` and `<script src>`, in page order, each once."""
    out = []
    for found in _TAG.finditer(html):
        name, attribute = found.group(1).lower(), found.group(2).lower()
        if (name == "a") != (attribute == "href"):
            continue
        raw = (found.group(3) or found.group(4) or found.group(5) or "").strip()
        url = resolve(base, raw.replace("&amp;", "&"))
        hint = hint_for(url) if url is not None else None
        if hint is not None and hint not in out:
            out.append(hint)
            if len(out) >= MAX_HINTS:
                break
    return out


def page_hints(page):
    """`detect_boards` of a page that was read, plus the page's own address when it ended on a board
    (a careers page that redirects to its Greenhouse board)."""
    base = parse_url(page.url)
    if base is None or not base.is_http:
        return []
    own = hint_for(base)
    hints = [own] if own is not None else []
    for hint in detect_boards(page.html, base):
        if hint not in hints and len(hints) < MAX_HINTS:
            hints.append(hint)
    return hints


def hint_for(url):
    """The board behind one address, built from the pattern table and checked by `check_source`.
    The board name is validated before it is put into the address, and the address must read back
    to that same name, so a name carrying `?`, `#` or `/` (decoded from `for=acme%3Ffor%3Devil`) can
    never make the hint name one board and add another."""
    from . import check_source

    if url is None or not url.is_http or url.username or url.password is not None or url.port is not None:
        return None
    host = url.host.rstrip(".").lower()
    segments = url.segments()
    first = segments[0] if segments else None
    kind = kind_for_host(host)
    if kind == "greenhouse" and host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        # `embed/job_board?for=<slug>` and `embed/job_board/js?for=<slug>`
        name = next((value for key, value in url.query_pairs() if key == "for"), None) if first == "embed" else first
        if not name or not is_board_name(name):
            return None
        address = f"{host}/{name}"
    elif kind in _SLUG_HOSTS and host == _SLUG_HOSTS[kind]:
        name = first
        if not name or not is_board_name(name):
            return None
        address = f"{host}/{name}"
    elif kind in ("recruitee", "personio"):
        try:
            name, suffix = board_host(host, recruitee.HOSTS if kind == "recruitee" else personio.HOSTS)
        except Fail:
            return None
        if name in VENDOR_LABELS:
            return None
        address = f"{name}.{suffix}"
    elif kind == "workday":
        try:
            board = workday.Board.parse(str(url))
            address = f"https://{board.host}/{board.site}"
            if workday.Board.parse(address) != board:
                return None
        except Fail:
            return None
        name = board.tenant
    else:
        return None
    # Never `career_page`: the job-page check calls this function.
    try:
        read_back = check_source(kind, address)
    except Fail:
        return None
    return _hint(kind, address) if read_back["kind"] == kind and read_back["name"] == name else None


def no_postings_status(hints):
    """What to say about a job page that has no postings but points to a board; None without one."""
    if not hints:
        return None
    label = hints[0]["label"]
    return f"No postings on the page. It links to a {label} board: add it as a {label} source"
