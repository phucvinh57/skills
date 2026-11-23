"""`yc`: Y Combinator's job listing (Work at a Startup).

YC has no public JSON API for it. Its own board, `https://www.ycombinator.com/jobs`, is plain
server-rendered HTML with one link per job (`/companies/<company>/jobs/<id>-<title>`), so the
default needs neither a browser nor a login. `workatastartup.com` itself refuses plain requests
(HTTP 406) and wants a signed-in visitor: that is `NeedsBrowser` here (jobs there are
`/jobs/<number>` or `/companies/<company>/jobs/...`).

Only links on the listing's own site that match those shapes are candidates; a posting's text is
read by `fetch_posting`.
"""
import re

from ..cli import Fail
from .base import Adapter, Cached, NeedsBrowser, candidate, parse_url, within
from .web import is_generic, links, normalize_page_url, page_url, plain, same_site, title_from_url

# The listing read when the source has no address.
DEFAULT_URL = "https://www.ycombinator.com/jobs"
# The sites this source reads.
SITES = ("https://www.ycombinator.com/", "https://www.workatastartup.com/")


def check_url(raw):
    """`raw` as the address of a YC listing (a `Url`), or why it is not one."""
    url = within("YC source", normalize_page_url, raw)
    if not on_yc(url):
        raise Fail(f"YC source: `{url.host}` is not a ycombinator.com or workatastartup.com address")
    return url


def on_yc(url):
    return any(same_site(parse_url(site), url) for site in SITES)


class Yc(Adapter):
    def __init__(self, source, http=None):
        """`Fail` for an address that is not on ycombinator.com or workatastartup.com."""
        super().__init__(http)
        self.url = check_url((source.get("url") or "").strip() or DEFAULT_URL)
        self._listing = Cached()

    def _jobs(self):
        def load():
            page = plain(self.net, str(self.url))
            found = candidates(page, self.url)
            if not found:
                raise NeedsBrowser(
                    "no job links were found in the page: it is probably drawn by JavaScript or wants a signed-in visitor",
                    url=str(self.url),
                )
            return found

        return self._listing.get(load)

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page(self._jobs(), prefs, cursor, page_size)


def candidates(page, site):
    """The job links of a listing page, read for the listing the user configured (`site`): a page
    from any other site (a redirect) and links that lead elsewhere are not followed."""
    base = page_url(page)
    if base is None or not on_yc(base) or not same_site(site, base):
        return []
    found = []
    for url, text in links(page.html, base):
        if not same_site(site, url):
            continue
        company = company_of(url)
        if company is None:
            continue
        title = title_from_url(url) if is_generic(text) else text
        if title:
            found.append(candidate(str(url), title, company))
    return found


def company_of(url):
    """The company of a job link (`""` when the link does not name it), or None when the link is
    not a job: `/companies/<company>/jobs/<job>` on either site, and `/jobs/<number>` on Work at a
    Startup."""
    segments = url.segments()
    if len(segments) == 4 and segments[0] == "companies" and segments[2] == "jobs":
        return company_name(segments[1])
    if len(segments) == 2 and segments[0] == "jobs" and re.fullmatch(r"[0-9]+", segments[1]) and "workatastartup" in url.host:
        return ""
    return None


def company_name(slug):
    """`candid-health` -> `Candid Health`."""
    return " ".join(word[:1].upper() + word[1:] for word in re.split(r"[-_]", slug) if word)
