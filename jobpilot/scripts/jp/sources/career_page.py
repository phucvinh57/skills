"""`career_page`: a job page the user added: a company's careers page, or any other page that lists
jobs (a job board's search, an aggregator). The candidates come from the first of these that gives
any, in order of cost:

1. the page read plain: its schema.org `JobPosting` JSON-LD (`jsonld`), else its links that look
   like postings;
2. the site's `sitemap.xml` (`sitemap`), except on a host shared by many companies (its sitemap
   lists every company's postings).

The app's third step renders the page in the user's browser (a job list that JavaScript draws, or a
wall on the plain read). The skill has no browser, so that step is `NeedsBrowser`: the agent may
read the page with its own browsing tool and add the jobs by hand.

The model never picks a URL: only addresses on the configured site (`web.same_site`: the host the
user entered or a subdomain of it) are candidates, whether a link, a JSON-LD `url` or a sitemap
`<loc>`; links and sitemap entries must also have a posting-shaped path
(`web.looks_like_posting`). A posting's text is read by `fetch_posting`.

When no attempt finds a posting, the adapter keeps the job boards the page points to (`boards`),
so the caller can suggest adding that board as a source. It never reads the board itself.
"""
import datetime

from .base import BROWSER_HINT, Adapter, Cached, NeedsBrowser, candidate, within
from .boards import no_postings_status, page_hints
from .jsonld import expired, job_postings
from .sitemap import read_sitemap
from .web import (
    check_site, is_generic, is_shared_host, links, looks_like_posting, normalize_page_url, page_url, plain, same_site,
    title_from_url,
)

# Most postings taken from one page: a page with more links than this is not a careers page.
MAX_POSTINGS = 500


class CareerPage(Adapter):
    def __init__(self, source, company, http=None):
        """`company` names the postings whose JSON-LD does not say (an aggregator's page lists many
        companies). `Fail` for an address that is not an http(s) page of a real site."""
        super().__init__(http)
        self.url = within("job page source", normalize_page_url, source.get("url", ""))
        within("job page source", check_site, self.url)
        self.company = company
        self._listing = Cached()
        self._hints = []

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page(self._listing.get(self._read), prefs, cursor, page_size)

    def board_hints(self):
        """The boards the last read pointed to, when it found no postings."""
        return [dict(hint) for hint in self._hints]

    def _read(self):
        """The candidates, trying the plain page and the sitemap in turn; `NeedsBrowser` when
        neither has any (with the boards the page points to)."""
        self._hints = []
        hints, wall = [], None
        # Postings the page's JSON-LD says are closed: the fallbacks would otherwise bring them back.
        closed = set()
        try:
            page = plain(self.net, str(self.url))
        except NeedsBrowser as err:
            page, wall = None, err
        if page is not None:
            hints = page_hints(page)
            found = self._candidates(page, closed)
            if found:
                return found
        # A host shared by many companies (linkedin.com, apply.workable.com, github.io) has one
        # sitemap for all of them: only the page itself says which postings are this source's.
        host = self.url.host.lower()
        while host.startswith("www."):
            host = host[4:]
        if not is_shared_host(host):
            found = [c for c in read_sitemap(self.net, self.url, self.url, self.company, MAX_POSTINGS) if c["url"] not in closed]
            if found:
                return found
        self._hints = hints
        if wall is not None:
            message = wall.message
        else:
            message = "no postings were found in the page or in the site's sitemap: the job list is probably drawn by JavaScript"
        status = no_postings_status(hints)
        hint = f"{status} (`{hints[0]['url']}`); or {BROWSER_HINT}" if status else None
        extra = {"status": wall.extra["status"]} if wall is not None and "status" in wall.extra else {}
        raise NeedsBrowser(message, hint, url=str(self.url), boards=[dict(h) for h in hints], **extra)

    def _candidates(self, page, closed):
        """The postings of `page`: its open JSON-LD postings when it has any on the configured site,
        else its same-site, posting-shaped links, titled by their text (or their address). The
        addresses of closed JSON-LD postings are added to `closed`, and no link to one is a candidate."""
        base = page_url(page)
        # The site is the one the user configured: a redirect to another site (or links that lead
        # there) does not move it, and a page read from another site has no candidates at all.
        if base is None or not same_site(self.url, base):
            return []
        found = self._structured(page, base, datetime.datetime.now(datetime.timezone.utc), closed)
        if found:
            return found
        for url, text in links(page.html, base):
            if len(found) >= MAX_POSTINGS:
                break
            if not looks_like_posting(self.url, base, url) or str(url) in closed:
                continue
            title = title_from_url(url) if is_generic(text) else text
            if title:
                found.append(candidate(str(url), title, self.company))
        return found

    def _structured(self, page, base, now, closed):
        """The page's `JobPosting` JSON-LD items that are still open at `now` and whose address is on
        the configured site (JSON-LD is page text: its `url` gets the same rule as a link). The
        addresses of the closed ones go to `closed`."""
        seen, found = set(), []
        for posting in job_postings(page.html, base):
            url = posting["url"]
            if url is None or not same_site(self.url, url) or url == self.url:
                continue
            if expired(posting, now):
                closed.add(str(url))
                continue
            if len(found) >= MAX_POSTINGS or str(url) in seen:
                continue
            seen.add(str(url))
            title = posting["title"] or title_from_url(url)
            if not title:
                continue
            # An aggregator's page lists many companies: the posting's own `hiringOrganization` first.
            found.append(candidate(str(url), title, posting["company"] or self.company, posting["location"]))
        return found
