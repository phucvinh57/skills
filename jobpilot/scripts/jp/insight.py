"""The company brief of a job: business domain, company facts, skills, interview process, from the
posting and the company's own pages.

Rule: unknown stays empty; nothing is guessed. The agent only extracts from the texts it is given,
and the code drops what it cannot ground: a number must be a whole number of one of the texts, a
stage or headquarters must be written in one of them, and sources are only pages that were read.
`domain` and `business_summary` are summaries of the posting, so they are kept as written.

Which pages are read is decided by code from the posting's own address and nothing else: posting
text and the agent never choose a URL (`company_urls`). A page that needs a browser, a login or a
captcha is skipped.

    pages = fetch_pages(company_urls(posting_url))       # [{"url", "text"}]
    insight, dropped = assemble(brief, jd_text, pages, posting_url)
"""
import ipaddress
import re
import urllib.parse

from . import http as net, ingest
from .cli import Fail

MAX_JD_CHARS = 12_000  # longest posting text shown for the brief
MAX_PAGE_CHARS = 5_000  # longest text taken from one company page
MIN_PAGE_CHARS = 400  # less text than this is a script shell, not a page
COMPANY_PATHS = ("about", "careers")  # pages of a company site worth reading, after its root

# ATS boards and job sites: their addresses carry a company slug and no company domain.
ATS_DOMAINS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com",
    "workday.com", "linkedin.com", "smartrecruiters.com", "workable.com", "workablemail.com", "icims.com",
    "jobvite.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "breezy.hr", "applytojob.com", "jazzhr.com",
    "taleo.net", "successfactors.com", "successfactors.eu", "personio.de", "personio.com", "comeet.co", "comeet.com",
    "eightfold.ai", "pinpointhq.com", "rippling.com", "oraclecloud.com", "dayforcehcm.com", "indeed.com",
    "glassdoor.com", "wellfound.com", "angel.co", "ziprecruiter.com", "topcv.vn", "itviec.com", "vietnamworks.com",
)
# Second-level suffixes where the registrable domain has three labels (`acme.co.uk`).
SECOND_LEVEL = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.nz", "co.jp", "co.kr", "co.in", "co.za", "com.br",
    "com.cn", "com.hk", "com.mx", "com.sg", "com.tr", "com.tw", "com.vn", "edu.vn", "net.vn", "org.vn",
)
# Site-hosting platforms: their customers live on subdomains (`acme.vercel.app`) or in a path
# (`github.com/acme`), so the registrable domain is the platform, not the company.
PLATFORMS = (
    "notion.site", "notion.so", "vercel.app", "github.io", "github.com", "gitlab.io", "gitlab.com", "bitbucket.org",
    "netlify.app", "herokuapp.com", "pages.dev", "workers.dev", "webflow.io", "wixsite.com", "squarespace.com",
    "wordpress.com", "blogspot.com", "medium.com", "substack.com", "carrd.co", "framer.website", "framer.app",
    "glitch.me", "onrender.com", "fly.dev", "web.app", "firebaseapp.com", "azurewebsites.net", "amazonaws.com",
    "cloudfront.net", "sites.google.com", "docs.google.com",
)


# Names of a private network, whatever they resolve to.
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")


def registrable_domain(host):
    """`careers.acme.co.uk` -> `acme.co.uk`."""
    host = host.strip().rstrip(".").lower()
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    keep = 3 if ".".join(labels[-2:]) in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def _under(host, domains):
    return next((domain for domain in domains if host == domain or host.endswith("." + domain)), None)


def is_ats_host(host):
    return _under(host.strip().rstrip(".").lower(), ATS_DOMAINS) is not None


def company_urls(posting_url):
    """The company pages to read: the root of the posting's company domain, `/about` and `/careers`
    (same scheme as the posting). A subdomain such as `careers.acme.com` maps to `acme.com`. Empty
    when the posting is missing, is not a public http(s) address, is on an ATS board or job site, or
    is a hosting platform's own address (`vercel.app`, `github.com/acme`). A tenant of a platform
    (`acme.vercel.app`) is the company's own site on that exact host."""
    try:
        url = net.checked_url(posting_url or "")
        parts = urllib.parse.urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except (net.HttpError, ValueError):
        return []
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        # An address literal is a site only in tests: fixtures serve all pages on one port.
        if not net.TEST_ALLOW_PRIVATE:
            return []
        shown = f"[{host}]" if ":" in host else host
        root = f"{parts.scheme}://{shown}:{port}/" if port else f"{parts.scheme}://{shown}/"
    else:
        host = host.rstrip(".")
        host = host[4:] if host.startswith("www.") else host
        platform = _under(host, PLATFORMS)
        if platform == host or host == "localhost" or host.endswith(PRIVATE_SUFFIXES):
            return []
        if platform:
            root = f"{parts.scheme}://{host}/"
        elif is_ats_host(host):
            return []
        else:
            root = f"{parts.scheme}://{registrable_domain(host)}/"
    return [root] + [root + path for path in COMPANY_PATHS]


_WALL_PHRASES = (
    "captcha", "verify you are human", "verify you are a human", "are you a robot", "just a moment",
    "checking your browser", "unusual traffic", "access denied", "enable javascript and cookies",
    "sign in to continue", "log in to continue", "please sign in", "please log in",
)


def is_wall(text):
    """A short page that is a captcha, bot check or login prompt rather than company content."""
    if len(text) > 2_000:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in _WALL_PHRASES)


def fetch_page(url, http=None):
    """The readable text of the HTML page at `url`, or None for anything else: a refused address, a
    failed request, an answer that is not success, a page that is not HTML, one with too little
    text, or a wall. Nothing here is an error: a page is optional."""
    http = http or net.client()
    try:
        response = http.get(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
    except net.HttpError:
        return None
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status != 200 or not (not content_type or "html" in content_type):
        return None
    text = ingest.html_to_text(response.text()).strip()
    if len(text) < MIN_PAGE_CHARS or is_wall(text):
        return None
    return text[:MAX_PAGE_CHARS]


def fetch_pages(urls, http=None):
    """`[{"url", "text"}]` of the pages that could be read; one that repeats an earlier page's text
    (`/about` redirecting to `/`) is left out."""
    pages = []
    for url in urls:
        text = fetch_page(url, http)
        if text and not any(page["text"] == text for page in pages):
            pages.append({"url": url, "text": text})
    return pages


# --- grounding -----------------------------------------------------------------------------------

def _one_line(text):
    return " ".join(str(text).split())


def tidy(text, limit):
    """One line, at most `limit` characters."""
    return _one_line(text)[:limit]


def tidy_list(items, count, limit):
    """Tidy items without blanks or repeats (case aside), at most `count` of them."""
    out = []
    for item in items:
        item = tidy(item, limit)
        if item and item.lower() not in [other.lower() for other in out]:
            out.append(item)
    return out[:count]


def numbers_in(text):
    """The numbers in `text`, as whole numbers without thousands separators ("1,200." is `1200`)."""
    return [found.replace(",", "").rstrip(".") for found in re.findall(r"\d[\d,.]*", text)]


class Evidence:
    """What the texts the agent was given say, to check its claims against."""

    def __init__(self, texts):
        self.lines = [_one_line(text).lower() for text in texts]
        self.numbers = {number for text in texts for number in numbers_in(text)}

    def has_phrase(self, value):
        """Is `value` written in a text: the same whole words in a row, in any case ("Series B" is
        not in "Series Bravo")?"""
        value = _one_line(value).lower()
        if not value:
            return False
        for line in self.lines:
            at = line.find(value)
            while at != -1:
                end = at + len(value)
                before = line[at - 1] if at else ""
                after = line[end] if end < len(line) else ""
                if not before.isalnum() and not after.isalnum():
                    return True
                at = line.find(value, at + 1)
        return False

    def has_figure(self, value):
        """Is every number of `value` a whole number of a text (50 is not in "150", 2015 is not in
        "20150")? A value without numbers has to be written in a text, like a phrase."""
        numbers = numbers_in(value)
        if not numbers:
            return self.has_phrase(value)
        return all(number in self.numbers for number in numbers)


def empty():
    return {
        "domain": "", "business_summary": "",
        "company": {"size": "", "stage": "", "funding": "", "hq": "", "founded": ""},
        "skills": {"required": [], "preferred": []},
        "interview": {"stages": [], "notes": "", "found": False},
        "sources": [],
    }


def has_content(insight):
    """Does the brief say anything (sources aside)?"""
    blank = empty()
    return any(insight[name] != blank[name] for name in ("domain", "business_summary", "company", "skills", "interview"))


def _text(value, where):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Fail(f"`{where}` must be text (an empty string when no text says it)")
    return value


def _strings(value, where):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Fail(f"`{where}` must be a list of strings")
    return value


def _object(value, where):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Fail(f"`{where}` must be an object")
    return value


def assemble(raw, jd_text, pages, posting_url=None, known_skills=None):
    """The agent's answer as a brief: tidy, grounded, with sources the code knows. Returns
    `(insight, dropped)`; `dropped` lists what was left out and why. `known_skills` is
    `(required, preferred)` when the job's analysis already gives them."""
    if not isinstance(raw, dict):
        raise Fail("the brief must be a JSON object", hint="see references/discover.md for the shape")
    company = _object(raw.get("company"), "company")
    skills = _object(raw.get("skills"), "skills")
    interview = _object(raw.get("interview"), "interview")
    found = interview.get("found", False)
    if not isinstance(found, bool):
        raise Fail("`interview.found` must be true or false")
    cited = raw.get("source_pages") or []
    if not isinstance(cited, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in cited):
        raise Fail("`source_pages` must be a list of page numbers")

    jd_text = (jd_text or "")[:MAX_JD_CHARS]
    evidence = Evidence([jd_text] + [page["text"] for page in pages])
    dropped = []

    def grounded(name, limit, check, why):
        value = tidy(_text(company.get(name), f"company.{name}"), limit)
        if value and not check(value):
            dropped.append({"field": f"company.{name}", "value": value, "why": why})
            return ""
        return value

    no_figure = "a number in it is in neither the posting nor the pages that were read"
    no_phrase = "it is written in neither the posting nor the pages that were read"
    facts = {
        "size": grounded("size", 80, evidence.has_figure, no_figure),
        "stage": grounded("stage", 80, evidence.has_phrase, no_phrase),
        "funding": grounded("funding", 80, evidence.has_figure, no_figure),
        "hq": grounded("hq", 100, evidence.has_phrase, no_phrase),
        "founded": grounded("founded", 80, evidence.has_figure, no_figure),
    }
    if known_skills:
        required, preferred = known_skills
    else:
        required, preferred = _strings(skills.get("required"), "skills.required"), _strings(skills.get("preferred"), "skills.preferred")
    stages = _strings(interview.get("stages"), "interview.stages")
    notes = _text(interview.get("notes"), "interview.notes")
    # "Not found" means nothing else about the process either.
    if found:
        stages, notes = tidy_list(stages, 12, 120), tidy(notes, 600)
    else:
        if stages or notes.strip():
            dropped.append({"field": "interview", "value": "stages / notes", "why": "`found` is false"})
        stages, notes = [], ""
    insight = {
        "domain": tidy(_text(raw.get("domain"), "domain"), 100),
        "business_summary": tidy(_text(raw.get("business_summary"), "business_summary"), 500),
        "company": facts,
        "skills": {"required": tidy_list(required, 30, 80), "preferred": tidy_list(preferred, 30, 80)},
        "interview": {"stages": stages, "notes": notes, "found": found and bool(stages or notes)},
        "sources": [],
    }
    if has_content(insight):
        urls = [posting_url] if posting_url else []
        for number in cited:
            if 1 <= number <= len(pages):
                urls.append(pages[number - 1]["url"])
            else:
                dropped.append({"field": "source_pages", "value": number, "why": "no page with this number was read"})
        for url in urls:
            if url not in insight["sources"]:
                insight["sources"].append(url)
    return insight, dropped
