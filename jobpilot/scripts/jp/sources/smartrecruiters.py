"""SmartRecruiters: the public postings API (`api.smartrecruiters.com/v1/companies/<id>/postings`).

Big companies list thousands of postings, so the listing is read one API page (`limit=100`) at a
time, each cached. The cursor is `<API offset>:<index into the role-filtered API page>`, so the same
cursor gives the same page. The listing has no description: a posting's text is one request for
that posting.
"""
from ..cli import Fail
from .base import Adapter, Cached, at, candidate, html_text, last_segment, pair_cursor, roles_of, text_of, title_fits_roles

BASE = "https://api.smartrecruiters.com"
# Where a posting is shown to people: `<BOARD>/<company id>/<posting id>`.
BOARD = "https://jobs.smartrecruiters.com"
# Postings per API page (the API's own limit).
API_PAGE = 100
# API pages one search reads at most while the roles filter out everything on them.
API_PAGES_PER_SEARCH = 3
# The sections of a job ad, in reading order.
SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


class SmartRecruiters(Adapter):
    def __init__(self, company_id, company, http=None, base=None):
        super().__init__(http)
        self.base = (base or BASE).rstrip("/")
        self.company_id, self.company = company_id, company
        self._pages = {}

    def _api_page(self, offset):
        def load():
            url = f"{self.base}/v1/companies/{self.company_id}/postings?limit={API_PAGE}&offset={offset}"
            body = self.net.get_json(url, "reading the SmartRecruiters postings")
            content = body.get("content") if isinstance(body, dict) else None
            if not isinstance(content, list):
                raise Fail("reading the SmartRecruiters postings: the SmartRecruiters answer has no posting list")
            total = body.get("totalFound")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                more = bool(content) and offset + API_PAGE < total
            else:
                more = len(content) >= API_PAGE
            return {"candidates": [found for found in map(self._candidate, content) if found], "more": more}

        return self._pages.setdefault(offset, Cached()).get(load)

    def _candidate(self, posting):
        posting_id, title = text_of(at(posting, "id")), text_of(at(posting, "name"))
        if not _valid_id(posting_id) or not title:
            return None
        company = text_of(at(posting, "company", "name")) or self.company
        labels = [text_of(at(posting, "experienceLevel", "label")), text_of(at(posting, "typeOfEmployment", "label"))]
        return candidate(
            f"{BOARD}/{self.company_id}/{posting_id}", title, company, _location(at(posting, "location")),
            ", ".join(label for label in labels if label),
        )

    def search(self, prefs, cursor=None, page_size=25):
        offset, skip = pair_cursor(cursor)
        want, roles, found = max(page_size, 1), roles_of(prefs), []
        for _ in range(API_PAGES_PER_SEARCH):
            page = self._api_page(offset)
            fitting = [c for c in page["candidates"] if title_fits_roles(c["title"], roles)]
            taken = fitting[skip:skip + want - len(found)]
            used = skip + len(taken)
            found += [dict(c) for c in taken]
            if used < len(fitting):
                return {"candidates": found, "next": f"{offset}:{used}"}
            if not page["more"]:
                return {"candidates": found, "next": None}
            offset, skip = offset + API_PAGE, 0
            if len(found) >= want:
                break
        return {"candidates": found, "next": f"{offset}:0"}

    def posting(self, candidate):
        posting_id = _posting_id(candidate.get("url", ""))
        if posting_id is None:
            return None
        url = f"{self.base}/v1/companies/{self.company_id}/postings/{posting_id}"
        job = self.net.get_json(url, "reading the SmartRecruiters posting")
        sections = at(job, "jobAd", "sections")
        if not isinstance(sections, dict):
            raise Fail("reading the SmartRecruiters posting: the SmartRecruiters posting has no job ad")
        body = ""
        for key in SECTIONS:
            section = sections.get(key)
            content = html_text(text_of(at(section, "text")))
            if not content:
                continue
            heading = text_of(at(section, "title"))
            if heading:
                body += f"## {heading}\n\n"
            body += f"{content}\n\n"
        if not body:
            return None
        title = text_of(at(job, "name")) or candidate.get("title", "")
        location = _location(at(job, "location")) or candidate.get("location", "")
        head = f"# {title}\n\nCompany: {candidate.get('company', '')}\nLocation: {location}\n"
        for label, key in (("Employment", "typeOfEmployment"), ("Experience", "experienceLevel")):
            value = text_of(at(job, key, "label"))
            if value:
                head += f"{label}: {value}\n"
        return f"{head}\n{body.rstrip()}"


def _valid_id(posting_id):
    """A posting id: letters and digits only (it goes into the API path)."""
    return bool(posting_id) and posting_id.isascii() and posting_id.isalnum()


def _location(place):
    """`fullLocation` (or city, region, country), with "(Remote)" or "(Hybrid)" when the posting says so."""
    if not isinstance(place, dict):
        return ""
    location = text_of(place.get("fullLocation"))
    if not location:
        location = ", ".join(part for part in (text_of(place.get(key)) for key in ("city", "region", "country")) if part)
    return with_mode(location, place)


def with_mode(location, flags):
    """`location` with "(Remote)" or "(Hybrid)" when `flags` says so and the location does not."""
    if flags.get("remote") is True:
        mode = "Remote"
    elif flags.get("hybrid") is True:
        mode = "Hybrid"
    else:
        return location
    if not location:
        return mode
    return location if mode.lower() in location.lower() else f"{location} ({mode})"


def _posting_id(url):
    """The posting id in a candidate's URL: `.../<id>` or `.../<id>-<title slug>`."""
    segment = last_segment(url)
    if segment is None:
        return None
    posting_id = segment.split("-")[0]
    return posting_id if _valid_id(posting_id) else None
