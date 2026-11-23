"""Greenhouse: the public board API (`boards-api.greenhouse.io/v1/boards/<slug>/jobs`).

The listing is small (no descriptions); a posting's text is one request for that job.
"""
from ..cli import Fail
from .base import Adapter, Cached, at, candidate, header, html_text, parse_url, text_of, unescape_markup

BASE = "https://boards-api.greenhouse.io"


class Greenhouse(Adapter):
    def __init__(self, slug, company, http=None, base=None):
        """`base`: the API origin (a test server). `company` names postings that carry no name."""
        super().__init__(http)
        self.base = (base or BASE).rstrip("/")
        self.slug, self.company = slug, company
        self._listing = Cached()

    def _jobs(self):
        def load():
            body = self.net.get_json(f"{self.base}/v1/boards/{self.slug}/jobs", "reading the Greenhouse board")
            jobs = body.get("jobs") if isinstance(body, dict) else None
            if not isinstance(jobs, list):
                raise Fail("reading the Greenhouse board: the Greenhouse board has no job list")
            return [found for found in map(self._candidate, jobs) if found]

        return self._listing.get(load)

    def _candidate(self, job):
        url, title = text_of(at(job, "absolute_url")), text_of(at(job, "title"))
        if not url or not title:
            return None
        company = text_of(at(job, "company_name")) or self.company
        return candidate(url, title, company, text_of(at(job, "location", "name")))

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page(self._jobs(), prefs, cursor, page_size)

    def posting(self, candidate):
        job_id = _job_id(candidate.get("url", ""))
        if job_id is None:
            return None
        job = self.net.get_json(f"{self.base}/v1/boards/{self.slug}/jobs/{job_id}", "reading the Greenhouse posting")
        body = html_text(unescape_markup(text_of(at(job, "content"))))
        if not body:
            return None
        title = at(job, "title") if isinstance(at(job, "title"), str) else candidate.get("title", "")
        location = at(job, "location", "name")
        if not isinstance(location, str):
            location = candidate.get("location", "")
        return f"{header(title, candidate.get('company', ''), location)}\n\n{body}"


def _job_id(url):
    """The numeric job id: `/jobs/4011001` on a board, or `?gh_jid=4011001` on a company's own page."""
    parsed = parse_url(url)
    if parsed is None or not parsed.is_http:
        return None

    def numeric(text):
        return bool(text) and text.isascii() and text.isdigit()

    for key, value in parsed.query_pairs():
        if key == "gh_jid":
            if numeric(value):
                return value
            break
    segments = parsed.segments()
    return segments[-1] if segments and numeric(segments[-1]) else None
