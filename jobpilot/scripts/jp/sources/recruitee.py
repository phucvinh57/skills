"""Recruitee: the public offers API of a company's board (`<company>.recruitee.com/api/offers/`).

The listing carries every description, so postings are read from it (one request, cached).
"""
import ipaddress
import re

from .. import http as net
from ..cli import Fail
from .base import SNIPPET_CHARS, Adapter, Cached, at, candidate, html_text, last_segment, one_line, parse_url, text_of
from .smartrecruiters import with_mode

# The board hosts: `<company>.recruitee.com`.
HOSTS = ("recruitee.com",)
# Tests only: the board's origin instead of `https://<company>.recruitee.com`.
ORIGIN = None


class Recruitee(Adapter):
    def __init__(self, origin, company, http=None):
        """`origin`: the company's board (`https://acme.recruitee.com`), built by code from a name
        `board_host` checked. `company` names postings that carry no company name."""
        super().__init__(http)
        self.origin, self.company = origin.rstrip("/"), company
        self._listing = Cached()

    def _postings(self):
        def load():
            body = self.net.get_json(f"{self.origin}/api/offers/", "reading the Recruitee offers")
            offers = body.get("offers") if isinstance(body, dict) else None
            if not isinstance(offers, list):
                raise Fail("reading the Recruitee offers: the Recruitee answer has no offer list")
            return [found for found in map(self._posting_of, offers) if found]

        return self._listing.get(load)

    def _posting_of(self, offer):
        slug, title = text_of(at(offer, "slug")), text_of(at(offer, "title"))
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug) or not title:
            return None
        url = posting_url(text_of(offer.get("careers_url")), slug) or f"{self.origin}/o/{slug}"
        company = text_of(offer.get("company_name")) or self.company
        location = _location(offer)
        description = html_text(text_of(offer.get("description")))
        requirements = html_text(text_of(offer.get("requirements")))
        full = f"# {title}\n\nCompany: {company}\nLocation: {location}\n"
        department = text_of(offer.get("department"))
        if department:
            full += f"Department: {department}\n"
        salary = salary_of(offer.get("salary"))
        if salary:
            full += f"Salary: {salary}\n"
        full += f"\n{description}"
        if requirements:
            full += f"\n\n## Requirements\n\n{requirements}"
        return {
            "slug": slug,
            "candidate": candidate(url, title, company, location, one_line(description, SNIPPET_CHARS)),
            # None when the offer has no description: the caller loads its page instead.
            "text": full.rstrip() if description or requirements else None,
        }

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page([p["candidate"] for p in self._postings()], prefs, cursor, page_size)

    def posting(self, candidate):
        url = candidate.get("url", "")
        wanted = last_segment(url)
        for posting in self._postings():
            if posting["candidate"]["url"] == url or (wanted is not None and posting["slug"] == wanted):
                return posting["text"]
        return None


def posting_url(careers_url, slug):
    """The offer's own `careers_url` when it is an https address of this offer (its path ends in
    `/o/<slug>`) on a public host name; it may be on the company's own domain. An IP address, this
    machine or a private network name, or anything else is not used."""
    url = parse_url(careers_url)
    if url is None or url.scheme != "https" or url.username or url.password is not None:
        return None
    try:
        ipaddress.ip_address(url.host.strip("[]"))
        return None
    except ValueError:
        pass
    if not net.is_public_host(url.host) or not url.path.rstrip("/").endswith(f"/o/{slug}"):
        return None
    return str(url.without_fragment())


def _location(offer):
    """`location` (or city, country), with "(Remote)" or "(Hybrid)" when the offer says so."""
    location = text_of(offer.get("location"))
    if not location:
        location = ", ".join(part for part in (text_of(offer.get(key)) for key in ("city", "country")) if part)
    return with_mode(location, offer)


def salary_of(salary):
    """`60000-80000 EUR per year`, when the offer publishes a range."""
    if not isinstance(salary, dict):
        return None

    def field(key):
        value = salary.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return ""

    low, high = field("min"), field("max")
    if not low and not high:
        return None
    if not high or low == high:
        line = low
    elif not low:
        line = high
    else:
        line = f"{low}-{high}"
    for key, prefix in (("currency", " "), ("period", " per ")):
        if field(key):
            line += prefix + field(key)
    return line
