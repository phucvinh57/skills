"""Personio: the public XML job feed of a company's board (`<company>.jobs.personio.de/xml`).

The feed carries the descriptions when the company publishes them, so postings are read from it
(one request, cached). Some companies leave them empty: then the caller loads the posting's page.
"""
from ..cli import Fail
from .base import SNIPPET_CHARS, Adapter, Cached, XmlError, candidate, html_text, last_segment, one_line, xml_events

# The board hosts: `<company>.jobs.personio.de` (the default) or `.com`.
HOSTS = ("jobs.personio.de", "jobs.personio.com")
# Tests only: the board's origin instead of `https://<company>.jobs.personio.de`.
ORIGIN = None

ACCEPT = "application/xml, text/xml;q=0.9"
# Deepest element nesting a feed may have (a real one is 5 deep, its descriptions a few more).
MAX_DEPTH = 64

_FIELDS = {
    "id": "id", "name": "name", "subcompany": "company", "office": "office", "department": "department",
    "employmentType": "employment_type", "seniority": "seniority", "schedule": "schedule",
}


class Personio(Adapter):
    def __init__(self, origin, company, http=None):
        """`origin`: the company's board (`https://acme.jobs.personio.de`), built by code from a
        name `board_host` checked. `company` names the postings when the feed does not."""
        super().__init__(http)
        self.origin, self.company = origin.rstrip("/"), company
        self._listing = Cached()

    def _postings(self):
        def load():
            xml = self.net.get_text(f"{self.origin}/xml", ACCEPT, "reading the Personio job feed")
            return [found for found in map(self._posting_of, parse_positions(xml)) if found]

        return self._listing.get(load)

    def _posting_of(self, position):
        job_id, name = position["id"], position["name"]
        if not (job_id and job_id.isascii() and job_id.isalnum()) or not name:
            return None
        company = position["company"] or self.company
        location = ", ".join(office for office in [position["office"]] + position["offices"] if office)
        kind = ", ".join(part for part in (position["department"], position["seniority"], position["schedule"]) if part)
        body = ""
        for heading, markup in position["descriptions"]:
            content = html_text(markup)
            if not content:
                continue
            if heading:
                body += f"## {heading}\n\n"
            body += f"{content}\n\n"
        text = None
        if body:
            head = f"# {name}\n\nCompany: {company}\nLocation: {location}\n"
            for label, key in (("Department", "department"), ("Employment", "employment_type"),
                               ("Seniority", "seniority"), ("Schedule", "schedule")):
                if position[key]:
                    head += f"{label}: {position[key]}\n"
            text = f"{head}\n{body.rstrip()}"
        found = candidate(f"{self.origin}/job/{job_id}", name, company, location, one_line(kind, SNIPPET_CHARS))
        return {"id": job_id, "candidate": found, "text": text}

    def search(self, prefs, cursor=None, page_size=25):
        return self._fitting_page([p["candidate"] for p in self._postings()], prefs, cursor, page_size)

    def posting(self, candidate):
        url = candidate.get("url", "")
        wanted = last_segment(url)
        for posting in self._postings():
            if posting["candidate"]["url"] == url or (wanted is not None and posting["id"] == wanted):
                return posting["text"]
        return None


def _text(element):
    return (element.text or "").strip()


def _flat(element):
    """The text inside an element; markup that is not wrapped in CDATA is read as text, its tags
    as spaces."""
    parts = [element.text or ""]
    for child in element:
        parts += [" ", _flat(child), " ", child.tail or ""]
    return "".join(parts)


def _position(element):
    position = {key: "" for key in _FIELDS.values()}
    position.update(offices=[], descriptions=[])
    for child in element:
        if child.tag in _FIELDS:
            position[_FIELDS[child.tag]] = _text(child)
        elif child.tag == "additionalOffices":
            position["offices"] += [_text(office) for office in child if office.tag == "office" and _text(office)]
        elif child.tag == "jobDescriptions":
            for description in child:
                if description.tag != "jobDescription":
                    continue
                heading = markup = ""
                for part in description:
                    if part.tag == "name":
                        heading = _text(part)
                    elif part.tag == "value":
                        markup = _flat(part).strip()
                position["descriptions"].append((heading, markup))
    return position


def parse_positions(xml):
    """The `<position>`s of a `<workzag-jobs>` feed, in document order."""
    positions = []
    try:
        for event, element, depth in xml_events(xml, MAX_DEPTH):
            if event == "start":
                if depth == 1 and element.tag != "workzag-jobs":
                    raise Fail("the Personio answer is not a job feed")
            elif depth == 2 and element.tag == "position":
                positions.append(_position(element))
                element.clear()
    except XmlError as err:
        if err.deep:
            raise Fail("the Personio answer is not a job feed: its elements nest too deep")
        raise Fail(f"the Personio answer is not readable XML: {err}")
    return positions
