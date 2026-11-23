"""Hacker News "Ask HN: Who is hiring?": the latest monthly thread through the Algolia API, one
top-level comment per candidate. A comment is the whole posting, so no page is fetched.
"""
from ..cli import Fail
from .base import (
    SNIPPET_CHARS, Adapter, Cached, at, candidate, header, html_text, mentions, mentions_lowered, one_line, page_of,
    roles_of, title_fits_roles,
)

BASE = "https://hn.algolia.com"
ITEM_URL = "https://news.ycombinator.com/item?id="
# Longest header line kept as the title.
TITLE_CHARS = 120


class HnHiring(Adapter):
    def __init__(self, http=None, base=None):
        super().__init__(http)
        self.base = (base or BASE).rstrip("/")
        self._thread = Cached()

    def _latest_thread(self):
        """The newest "Who is hiring?" story posted by `whoishiring`."""
        url = f"{self.base}/api/v1/search_by_date?query=%22who%20is%20hiring%22&tags=story,author_whoishiring&hitsPerPage=10"
        body = self.net.get_json(url, "looking for the Who is hiring thread")
        hits = at(body, "hits")
        if not isinstance(hits, list):
            raise Fail("looking for the Who is hiring thread: the Hacker News search has no hits")
        threads = [hit for hit in hits if isinstance(at(hit, "title"), str) and "who is hiring" in hit["title"].lower()]

        def created(hit):
            stamp = hit.get("created_at_i")
            return stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else 0

        newest = max(reversed(threads), key=created, default=None)
        thread_id = at(newest, "objectID")
        if not (isinstance(thread_id, str) and thread_id.isascii() and thread_id.isdigit()):
            raise Fail("no Who is hiring thread was found on Hacker News")
        return thread_id

    def _comments(self):
        def load():
            thread = self.net.get_json(f"{self.base}/api/v1/items/{self._latest_thread()}", "reading the Who is hiring thread")
            children = at(thread, "children")
            if not isinstance(children, list):
                raise Fail("reading the Who is hiring thread: the thread has no comments")
            return [found for found in map(_comment, children) if found]

        return self._thread.get(load)

    def search(self, prefs, cursor=None, page_size=25):
        prefs = prefs or {}
        skills = [s for key in ("must_skills", "nice_skills") for s in prefs.get(key) or [] if isinstance(s, str)]
        roles = roles_of(prefs)
        fitting = [dict(c["candidate"]) for c in self._comments() if _relevant(c, skills, roles)]
        page, following = page_of(fitting, cursor, page_size)
        return {"candidates": page, "next": following}

    def posting(self, candidate):
        url = candidate.get("url", "")
        comment_id = url[len(ITEM_URL):] if url.startswith(ITEM_URL) else ""
        for comment in self._comments():
            if comment["id"] == comment_id:
                found = comment["candidate"]
                return f"{header(found['title'], found['company'], found['location'])}\n\n{comment['text']}"
        raise Fail("the comment is no longer in the current Who is hiring thread")


def _comment(child):
    """A top-level comment of the thread; None for a deleted one."""
    comment_id, raw = at(child, "id"), at(child, "text")
    if not isinstance(comment_id, int) or isinstance(comment_id, bool) or not isinstance(raw, str):
        return None
    text = html_text(raw)
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    # The first line is, by HN convention, `Company | Role | Location | ...`.
    first = one_line(lines[0])
    return {
        "id": str(comment_id), "header": first, "text": text, "lower": text.lower(),
        "candidate": _candidate(str(comment_id), first, " ".join(lines[1:])),
    }


def _candidate(comment_id, first, rest):
    """`Company | Role | Location | ...` -> a candidate. Every part of the header stays in the
    title, so nothing the poster wrote is lost."""
    parts = [part.strip() for part in first.split("|") if part.strip()]
    company = one_line(parts[0] if parts else "", 60)
    location = next(
        (one_line(part, 80) for part in parts[1:] if any(mentions(part, key) for key in ("remote", "onsite", "on-site", "hybrid"))),
        "",
    )
    return candidate(ITEM_URL + comment_id, one_line(first, TITLE_CHARS), company, location, one_line(rest, SNIPPET_CHARS))


def _relevant(comment, skills, roles):
    """Does a comment fit the preferences? Free text, so a skill anywhere or a role word in the
    header counts; no skills and no roles means everything does."""
    if not skills and not roles:
        return True
    return any(mentions_lowered(comment["lower"], skill) for skill in skills) or (
        bool(roles) and title_fits_roles(comment["header"], roles)
    )
