"""Ashby: the public job-board API (`api.ashbyhq.com/posting-api/job-board/<slug>`).

The listing carries every description (and the pay range when the company publishes it), so
postings are read from it (one request, cached).
"""
from ..cli import Fail
from .base import SNIPPET_CHARS, Adapter, Cached, at, candidate, last_segment, one_line, text_of

BASE = "https://api.ashbyhq.com"


class Ashby(Adapter):
    def __init__(self, slug, company, http=None, base=None):
        super().__init__(http)
        self.base = (base or BASE).rstrip("/")
        self.slug, self.company = slug, company
        self._listing = Cached()

    def _postings(self):
        def load():
            url = f"{self.base}/posting-api/job-board/{self.slug}?includeCompensation=true"
            body = self.net.get_json(url, "reading the Ashby job board")
            jobs = body.get("jobs") if isinstance(body, dict) else None
            if not isinstance(jobs, list):
                raise Fail("reading the Ashby job board: the Ashby board has no job list")
            return [found for found in map(self._posting_of, jobs) if found]

        return self._listing.get(load)

    def _posting_of(self, job):
        if at(job, "isListed") is False:
            return None
        url, title = text_of(at(job, "jobUrl")), text_of(at(job, "title"))
        if not url or not title:
            return None
        location = text_of(at(job, "location"))
        if at(job, "isRemote") is True and "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"
        description = text_of(at(job, "descriptionPlain"))
        pay = text_of(at(job, "compensation", "compensationTierSummary"))
        full = f"# {title}\n\nCompany: {self.company}\nLocation: {location}\n"
        if pay:
            full += f"Compensation: {pay}\n"
        full += f"\n{description}"
        return {
            "candidate": candidate(url, title, self.company, location, one_line(description, SNIPPET_CHARS)),
            "text": full,
        }

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page([p["candidate"] for p in self._postings()], prefs, cursor, page_size)

    def posting(self, candidate):
        wanted = last_segment(candidate.get("url", ""))
        if wanted is None:
            return None
        for posting in self._postings():
            if last_segment(posting["candidate"]["url"]) == wanted:
                return posting["text"]
        return None
