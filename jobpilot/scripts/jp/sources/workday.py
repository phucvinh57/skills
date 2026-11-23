"""Workday: the job search a Workday board page itself calls
(`POST https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`).

It is not a documented API, so the adapter is best effort: an answer of another shape is a clear
error, never a guess. The board is searched once per wanted role (the search text comes from the
preferences only), 20 postings at a time; a posting's text is one request for that posting. The
adapter keeps no state: a posting two roles find is listed twice and the caller's dedup keeps one,
so the same cursor gives the same page. Every address is built here from the board the user entered
and the posting's path; nothing in an answer chooses a host.
"""
import re

from ..cli import Fail
from .base import Adapter, at, candidate, html_text, pair_cursor, parse_url, roles_of, text_of, title_fits_roles, with_scheme

# The most postings Workday returns per request (it answers 400 above that).
PAGE_LIMIT = 20
# How deep one role's search goes.
MAX_OFFSET_PER_ROLE = 200
# How deep the search goes when no role is set (everything on the board, newest first).
MAX_OFFSET_NO_ROLES = 100

UNKNOWN_FORMAT = "Workday answered in an unknown format"
# Tests only: where the API calls go instead of `https://<board host>`.
ORIGIN = None


def _is_name(text):
    return re.fullmatch(r"[A-Za-z0-9_-]+", text) is not None


def _is_data_center(label):
    """`wd1`, `wd5`, `wd103`, ..."""
    return re.fullmatch(r"wd[0-9]{1,3}", label) is not None


def _is_locale(text):
    """`en-US`, `fr-FR`, `de`."""
    return re.fullmatch(r"[A-Za-z]{2}(-[A-Za-z]{2})?", text) is not None


def is_posting_path(path):
    """A posting path as Workday lists it (`/job/US-CA-Remote/Engineer_JR1`): it only ever extends
    the board's own path, never leaves it (no dot segments, not even percent-encoded ones)."""
    return (
        path.startswith("/")
        and ".." not in path
        and "%2e" not in path.lower()
        and "//" not in path
        and all("!" <= ch <= "~" and ch not in "?#\\" for ch in path)
    )


class Board:
    """A Workday board: `https://<tenant>.wd<N>.myworkdayjobs.com/[<locale>/]<site>`."""

    def __init__(self, host, tenant, site):
        self.host, self.tenant, self.site = host, tenant, site

    def __eq__(self, other):
        return isinstance(other, Board) and (self.host, self.tenant, self.site) == (other.host, other.tenant, other.site)

    def __repr__(self):
        return f"Board({self.host!r}, {self.tenant!r}, {self.site!r})"

    @classmethod
    def parse(cls, raw):
        """Read the board URL the user entered (with or without `https://`). Only
        `<tenant>.wd<N>.myworkdayjobs.com` hosts are accepted, and the tenant and site are plain
        names (`[A-Za-z0-9_-]`), so the API path cannot be walked out of."""
        raw = (raw or "").strip()
        if not raw:
            raise Fail("no Workday board is set: enter the board URL, e.g. https://acme.wd5.myworkdayjobs.com/External")
        url = parse_url(with_scheme(raw))
        if url is None or not url.is_http or url.port is not None or url.username or url.password is not None:
            raise Fail(f"`{raw}` is not a Workday board URL")
        labels = url.host.split(".")
        if not (len(labels) == 4 and labels[2:] == ["myworkdayjobs", "com"] and _is_name(labels[0]) and _is_data_center(labels[1])):
            raise Fail(
                f"`{url.host}` is not a Workday board: the address looks like https://<company>.wd<N>.myworkdayjobs.com/<site>"
            )
        segments = url.segments()
        if len(segments) >= 2 and _is_locale(segments[0]):
            site = segments[1]
        else:
            site = segments[0] if segments else ""
        if not site or _is_locale(site):
            raise Fail(f"the board's site name is missing in `{raw}` (https://{url.host}/<site>)")
        if not _is_name(site):
            raise Fail(f"`{site}` is not a Workday site name")
        return cls(url.host, labels[0], site)

    def search_url(self):
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, path):
        """The API address of a posting; None when `path` would not stay under the board's API path."""
        return self.under(f"/wday/cxs/{self.tenant}/{self.site}", path)

    def page_url(self, path):
        """The posting's page on the board; None when `path` would not stay under the board's site."""
        return self.under(f"/{self.site}", path)

    def under(self, prefix, path):
        """`https://<host><prefix><path>`, checked once more after the address has been read the way
        a browser reads it (dot segments resolved, also percent-encoded ones): the host is the
        board's and the path is still under `prefix/`."""
        if not is_posting_path(path):
            return None
        url = parse_url(f"https://{self.host}{prefix}{path}")
        if url is None or url.host != self.host or not url.path.startswith(prefix + "/"):
            return None
        return str(url)

    def path_of(self, page_url):
        """The posting path of a page URL made by `page_url`; None for any other address."""
        url = parse_url(page_url)
        if url is None or not url.is_http or url.host != self.host:
            return None
        if not url.path.startswith("/" + self.site):
            return None
        path = url.path[len(self.site) + 1:]
        return path if is_posting_path(path) else None


def queries(roles):
    """One search per wanted role, in the order of the preferences; one empty search when none is set."""
    seen, wanted = set(), []
    for role in roles:
        role = role.strip()
        if role and role.lower() not in seen:
            seen.add(role.lower())
            wanted.append(role)
    return wanted or [""]


class Workday(Adapter):
    def __init__(self, board, company, http=None, origin=None):
        """`company`: the name given to every posting (the source's name, or the tenant). `origin`:
        where the API calls go (a test server) instead of the board's own host."""
        super().__init__(http)
        self.board, self.company = board, company
        self.origin = (origin or ORIGIN or "").rstrip("/")

    def _api(self, url):
        home = f"https://{self.board.host}"
        return self.origin + url[len(home):] if self.origin else url

    def _candidate(self, posting):
        """A listed posting; None without a title or a path that stays on the board."""
        title, path = text_of(at(posting, "title")), text_of(at(posting, "externalPath"))
        if not title:
            return None
        url = self.board.page_url(path)
        if url is None or self.board.detail_url(path) is None:
            return None
        # The snippet is "Posted 3 Days Ago", as Workday writes it.
        return candidate(url, title, self.company, text_of(at(posting, "locationsText")), text_of(at(posting, "postedOn")))

    def search(self, prefs, cursor=None, page_size=25):
        roles = roles_of(prefs)
        wanted = queries(roles)
        role, offset = pair_cursor(cursor)
        if role >= len(wanted):
            return {"candidates": [], "next": None}
        query = wanted[role]
        limit = min(max(page_size, 1), PAGE_LIMIT)
        max_offset = MAX_OFFSET_PER_ROLE if query else MAX_OFFSET_NO_ROLES
        body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": query}
        answer = self.net.post_json(self._api(self.board.search_url()), body, "reading the Workday board")
        postings = at(answer, "jobPostings")
        if not isinstance(postings, list):
            raise Fail(f"reading the Workday board: {UNKNOWN_FORMAT}")
        # Some boards only count on the first page; 0 then means "not said".
        total = at(answer, "total")
        total = total if isinstance(total, int) and not isinstance(total, bool) and total > 0 else 0
        found = [c for c in map(self._candidate, postings) if c and title_fits_roles(c["title"], roles)]
        end = offset + len(postings)
        if len(postings) >= limit and end < max_offset and (total == 0 or end < total):
            following = f"{role}:{end}"
        else:
            following = f"{role + 1}:0" if role + 1 < len(wanted) else None
        return {"candidates": found, "next": following}

    def posting(self, candidate):
        path = self.board.path_of(candidate.get("url", ""))
        url = self.board.detail_url(path) if path else None
        if url is None:
            return None
        answer = self.net.get_json(self._api(url), "reading the Workday posting")
        info = at(answer, "jobPostingInfo")
        if not isinstance(info, dict):
            raise Fail(f"reading the Workday posting: {UNKNOWN_FORMAT}")
        # `externalUrl` is not read: the posting's address is built from the board, so an answer
        # cannot send the user or the caller to another host.
        body = html_text(text_of(info.get("jobDescription")))
        if not body:
            return None
        title = text_of(info.get("title")) or candidate.get("title", "")
        more = info.get("additionalLocations")
        places = [info.get("location")] + (more if isinstance(more, list) else [])
        places = [place.strip() for place in places if isinstance(place, str) and place.strip()]
        location = "; ".join(places) if places else candidate.get("location", "")
        head = f"# {title}\n\nCompany: {candidate.get('company', '')}\nLocation: {location}"
        if text_of(info.get("timeType")):
            head += f"\nTime type: {text_of(info.get('timeType'))}"
        return f"{head}\n\n{body}"
