"""Lever: the public postings API (`api.lever.co/v0/postings/<slug>?mode=json`).

The listing carries every description, so postings are read from it (one request, cached).
"""
from ..cli import Fail
from .base import SNIPPET_CHARS, Adapter, Cached, at, candidate, header, html_text, last_segment, one_line, text_of

BASE = "https://api.lever.co"


class Lever(Adapter):
    def __init__(self, slug, company, http=None, base=None):
        super().__init__(http)
        self.base = (base or BASE).rstrip("/")
        self.slug, self.company = slug, company
        self._listing = Cached()

    def _postings(self):
        def load():
            body = self.net.get_json(f"{self.base}/v0/postings/{self.slug}?mode=json", "reading the Lever postings")
            if not isinstance(body, list):
                raise Fail("reading the Lever postings: the Lever answer is not a list of postings")
            return [found for found in map(self._posting_of, body) if found]

        return self._listing.get(load)

    def _posting_of(self, posting):
        url, title = text_of(at(posting, "hostedUrl")), text_of(at(posting, "text"))
        if not url or not title:
            return None
        location = text_of(at(posting, "categories", "location"))
        if not location:
            every = at(posting, "categories", "allLocations")
            location = ", ".join(place for place in every if isinstance(place, str)) if isinstance(every, list) else ""
        if at(posting, "workplaceType") == "remote" and "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"
        description = text_of(at(posting, "descriptionPlain"))
        body = description
        lists = at(posting, "lists")
        for part in lists if isinstance(lists, list) else []:
            body += f"\n\n{text_of(at(part, 'text'))}\n{html_text(text_of(at(part, 'content')))}"
        extra = text_of(at(posting, "additionalPlain"))
        if extra:
            body += f"\n\n{extra}"
        return {
            "id": text_of(at(posting, "id")),
            "candidate": candidate(url, title, self.company, location, one_line(description, SNIPPET_CHARS)),
            "text": f"{header(title, self.company, location)}\n\n{body.strip()}",
        }

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page([p["candidate"] for p in self._postings()], prefs, cursor, page_size)

    def posting(self, candidate):
        url = candidate.get("url", "")
        wanted = last_segment(url)
        for posting in self._postings():
            if (wanted is not None and posting["id"] == wanted) or posting["candidate"]["url"] == url:
                return posting["text"]
        return None
