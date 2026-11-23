"""The web-page side of the page adapters (`yc`, `career_page`) and the generic posting read:
reading a page, finding the links on it that are postings, and the same-site rule.

**Code decides which pages are visited.** A link is only a candidate when it is on the same site as
the address the user configured (`same_site`) and its address looks like a posting
(`looks_like_posting`); the page's text never chooses a URL, and neither does the model.

A page is read with a plain HTTP request. A wall (HTTP 401, 403, 406, 429, 503) or a page that
JavaScript draws is `NeedsBrowser`: the skill has no browser, and a login, captcha or block page is
never worked around.
"""
import ipaddress
import re
import tempfile

from .. import ingest
from ..cli import Fail
from . import jsonld
from .base import NeedsBrowser, Net, Url, header, one_line, parse_url, with_scheme

# `Accept` only: any other header would make the client refuse a redirect to another origin.
ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
POSTING_ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"

# Largest posting page read.
MAX_POSTING_BYTES = 10 * 1024 * 1024
# Shortest `JobPosting` description (as text) that stands for the whole posting. Many sites put a
# one-line teaser there; below this the page itself is read.
MIN_STRUCTURED_CHARS = 400
# Shortest page text that is a posting; less is a page JavaScript draws, or a bot wall.
MIN_PAGE_CHARS = 400
# Longest link text kept.
TITLE_CHARS = 160


class Page:
    """A page that was read: its address after redirects, and its HTML."""

    def __init__(self, url, html):
        self.url, self.html = url, html


def plain(net, url):
    """A plain HTTP read. A wall (a bot check or login answer) is `NeedsBrowser`."""
    response = net.get(url, ACCEPT)
    return Page(response.url, response.text())


def page_url(page):
    url = parse_url(page.url)
    return url if url is not None and url.is_http else None


def normalize_page_url(raw):
    """`https://` when the scheme is missing; http(s) only, with a host."""
    raw = (raw or "").strip()
    if not raw:
        raise Fail("no page address is set: enter the address of a page that lists jobs")
    url = parse_url(with_scheme(raw))
    if url is None:
        raise Fail(f"`{raw}` is not a valid address")
    if not url.is_http:
        raise Fail("the page address must be http or https")
    return url


# ----------------------------------------------------------------------------------------- links

def resolve(base, href):
    """`href` as an absolute http(s) address with a host, resolved against `base`; None for an
    empty one, a fragment, and `javascript:`, `mailto:`, `tel:` and `data:` links."""
    if not href or href.startswith("#"):
        return None
    if href.lower().startswith(("javascript:", "mailto:", "tel:", "data:")):
        return None
    url = base.join(href)
    return url if url is not None and url.is_http else None


def links(html, base):
    """The links of `html` as `(Url, text)`, resolved against `base`: http(s) only, no fragments,
    each address once (the first one that has a telling text keeps it), in page order. A link to
    the page itself (`#top`) is left out."""
    here = str(base.without_fragment())
    out, where = [], {}
    for address, text in ingest.html_links(html, str(base)):
        url = parse_url(address)
        if url is None or not url.is_http:
            continue
        url = url.without_fragment()
        key = str(url)
        if key == here:
            continue
        text = one_line(text, TITLE_CHARS)
        if key in where:
            index = where[key]
            if is_generic(out[index][1]):
                out[index] = (url, text)
        else:
            where[key] = len(out)
            out.append((url, text))
    return out


_GENERIC = {
    "apply", "apply now", "apply here", "learn more", "read more", "more", "details", "view", "view job", "view details",
    "view role", "see details", "see more", "view all jobs", "see all jobs", "all jobs", "next", "previous", "back",
    "open", "link",
}


def is_generic(text):
    """A link text that says nothing about the job."""
    kept = "".join(ch for ch in text.strip().lower() if ch.isalnum() or ch.isspace())
    kept = " ".join(kept.split())
    return not kept or kept in _GENERIC


# ---------------------------------------------------------------------------- the same-site rule

# Job boards and mailers: a host here is shared by many companies.
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
# Hosting platforms where every customer is a subdomain: the platform's own name stands for all of
# them. (Not exhaustive: a customer's own subdomain, `acme.github.io`, is fine.)
PLATFORM_ROOTS = (
    "github.io", "gitlab.io", "vercel.app", "netlify.app", "pages.dev", "workers.dev", "herokuapp.com", "onrender.com",
    "fly.dev", "web.app", "firebaseapp.com", "appspot.com", "azurewebsites.net", "cloudfront.net", "amazonaws.com",
    "blogspot.com", "wordpress.com", "wixsite.com", "squarespace.com", "webflow.io", "notion.site", "myshopify.com",
    "carrd.co", "glitch.me",
)


def registrable_domain(host):
    """`careers.acme.co.uk` -> `acme.co.uk`."""
    host = host.strip().rstrip(".").lower()
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    keep = 3 if ".".join(labels[-2:]) in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def is_ats_host(host):
    host = host.strip().rstrip(".").lower()
    return any(host == domain or host.endswith("." + domain) for domain in ATS_DOMAINS)


def bare_host(url):
    """The host without a leading `www.`."""
    host = url.host.rstrip(".").lower()
    return host[4:] if host.startswith("www.") else host


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def not_a_site(host):
    """Why `host` (without `www.`) cannot be the address of a site at all: a single-word name or a
    public suffix (`co.uk`). None for any other name and for an IP address."""
    host = host.rstrip(".").lower()
    if _is_ip(host):
        return None
    if "." not in host:
        return "a single-word name, not a company site"
    # `x.<host>` is its own registrable domain only under a public suffix (`co.uk`, `com.vn`).
    probe = "x." + host
    if registrable_domain(probe) == probe:
        return "a public domain ending shared by many sites"
    return None


def is_shared_host(host):
    """Is `host` (without `www.`) shared by many companies, so that its subdomains are other sites:
    a hosting platform's own name (`github.io`) or a job board or aggregator (`linkedin.com`,
    `boards.greenhouse.io`)? Only its exact host is "the same site"."""
    host = host.rstrip(".").lower()
    return host in PLATFORM_ROOTS or is_ats_host(host)


def check_site(url):
    """Fail for an address that is not a site at all. Any other page may be a job source."""
    why = not_a_site(bare_host(url))
    if why:
        raise Fail(f"`{url.host}` is {why}: enter the address of a page that lists jobs")


def same_site(site, link):
    """Is `link` on the site `site`, the address the user **configured**? The same host (ignoring
    `www.`), or a subdomain of it. The parent of a configured subdomain, its siblings and look-alike
    names are other sites. On a host shared by many companies only that exact host counts.
    Redirects and links found on a page do not move `site`."""
    site, link = bare_host(site), bare_host(link)
    if not site or not_a_site(site):
        return False
    if is_shared_host(site):
        return link == site
    return link == site or link.endswith("." + site)


# Path words that mark a place where jobs live.
JOB_WORDS = (
    "job", "jobs", "career", "careers", "position", "positions", "opening", "openings", "vacancy", "vacancies",
    "posting", "postings", "role", "roles",
)
_FILES = ("css", "js", "png", "jpg", "jpeg", "gif", "svg", "ico", "webp", "woff", "woff2", "xml", "json", "zip")
_NOT_POSTINGS = ("search", "page", "all", "teams", "locations", "benefits", "culture", "life", "internships")
_ID = re.compile(r"[0-9]{4,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _is_job_word(segment):
    return any(token in JOB_WORDS for token in re.split(r"[-_]", segment))


def looks_like_posting(site, listing, link):
    """Does `link` look like one posting of the listing at `listing`? On the configured `site`,
    with a path that goes below a jobs-ish segment (`/careers/senior-engineer`, `/jobs/4011001`) or
    ends in a long id under a jobs-ish path, and not the listing itself nor a file."""
    if not same_site(site, link):
        return False
    segments = [segment.lower() for segment in link.segments()]
    if not segments or segments == [segment.lower() for segment in listing.segments()]:
        return False
    last = segments[-1]
    if "." in last and last.rsplit(".", 1)[1] in _FILES:
        return False
    # Listing pagination and search results are listings, not postings.
    if last in _NOT_POSTINGS:
        return False
    first = next((index for index, segment in enumerate(segments) if _is_job_word(segment)), None)
    if first is not None and first + 1 < len(segments):
        return True  # something below the jobs-ish segment
    # The jobs-ish segment is the last one: a posting only when it carries an id (`/open-role-4011001`).
    return bool(_ID.search(last)) and _is_job_word(last)


def title_from_url(url):
    """A title for a link without a usable text: the last path segment's words without a leading id
    (`/jobs/aaMaqQu-software-engineering-intern` -> "software engineering intern"). None when the
    segment has no words."""
    segments = url.segments()
    if not segments:
        return None
    parts = [word for word in re.split(r"[-_+]", segments[-1]) if word]
    if len(parts) > 1 and (any(ch.isascii() and ch.isdigit() for ch in parts[0]) or _mixed_case_id(parts[0])):
        parts = parts[1:]
    title = " ".join(parts)
    return title if any(ch.isalpha() for ch in title) else None


def _mixed_case_id(word):
    return (
        len(word) >= 5
        and any(ch.isascii() and ch.isupper() for ch in word[1:])
        and any(ch.isascii() and ch.islower() for ch in word)
    )


# ---------------------------------------------------------------------------- the posting's page

def structured(html, address):
    """What a posting page's `JobPosting` JSON-LD gives, when the page has exactly one:
    `(posting, full)`. `full` says its description stands for the whole posting (at least
    `MIN_STRUCTURED_CHARS` of text); otherwise it is a teaser and only the posting's header lines
    help. `(None, False)` for no posting, several (a listing, or related jobs), or one that says
    nothing. A posting whose `validThrough` has passed is still read (the header shows the date)."""
    base = parse_url(address)
    if base is None or not base.is_http:
        return None, False
    postings = jsonld.job_postings(html, base)
    if len(postings) != 1:
        return None, False
    one = postings[0]
    if len(jsonld.description_text(one).strip()) >= MIN_STRUCTURED_CHARS:
        return one, True
    return (one, False) if jsonld.header_text(one) else (None, False)


def _page_text(response, raw):
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "application/pdf" in content_type or response.body[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as temp:
            temp.write(response.body)
            temp.flush()
            return ingest.pdf_text(temp.name).strip()
    if "html" in content_type or "xml" in content_type or not content_type or ingest.looks_like_html(raw):
        return ingest.html_to_text(raw).strip()
    return ingest.clean(raw).strip()


def fetch_posting(candidate, http=None):
    """The posting behind `candidate["url"]` as text, for an adapter whose `posting()` is None.

    The text starts like every adapter's: `# {title}`, `Company:`, `Location:` (from the candidate,
    else from the page's own `JobPosting` data), then the other lines that data gives (remote,
    employment type, salary, dates), a blank line and the body. The body is the `JobPosting`
    description when the page carries exactly one posting with a full description (cleaner than
    reading the page), else the page's own text.

    `NeedsBrowser` for a wall and for a page with too little text (drawn by JavaScript)."""
    net = http if isinstance(http, Net) else Net(http)
    url = parse_url(candidate.get("url", ""))
    if url is None or not url.is_http:
        raise Fail("only http(s) links are read")
    response = net.get(str(url), POSTING_ACCEPT)
    if len(response.body) > MAX_POSTING_BYTES:
        raise Fail("the page is larger than 10 MB")
    raw = response.text()
    content_type = (response.headers.get("Content-Type") or "").lower()
    posting, full = (None, False)
    if "html" in content_type or not content_type:
        posting, full = structured(raw, response.url)
    if full:
        body = jsonld.description_text(posting)
    else:
        body = _page_text(response, raw)
        # The header does not count toward the page's own length: a page that needs a browser still does.
        if len(body) < MIN_PAGE_CHARS:
            raise NeedsBrowser(
                f"only {len(body)} characters of text were found (the page is probably drawn by JavaScript)",
                url=str(url),
            )
    found = posting or {}
    head = header(
        candidate.get("title") or found.get("title", ""),
        candidate.get("company") or found.get("company", ""),
        candidate.get("location") or found.get("location", ""),
    )
    more = jsonld.header_text(posting, skip=("Title", "Company", "Location")) if posting else ""
    if more:
        head += "\n" + more
    return f"{head}\n\n{body}"

