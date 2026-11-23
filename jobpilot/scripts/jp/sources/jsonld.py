"""schema.org `JobPosting` items in a page's `<script type="application/ld+json">` blocks. Many
careers pages and hiring systems publish them for search engines: a listing page as an `ItemList`
of postings, a posting page as one `JobPosting` with its full description.

The blocks are page text, so everything here is untrusted data: a posting's `url` is only an
address the caller still checks (the same-site rule for a listing), and the description is HTML
that only ever becomes text.
"""
import datetime
import json
import re

from .base import html_text, one_line, unescape_markup

# Largest script block read: a bigger one is skipped (a site's whole catalogue, or not data).
MAX_BLOCK_BYTES = 64 * 1024
# Most postings taken from one page.
MAX_POSTINGS = 500
# How deep the JSON is walked looking for postings (`@graph` in an `ItemList` in an array ...).
MAX_DEPTH = 6
# Longest single-line field kept (title, company, location ...).
FIELD_CHARS = 200

_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_LD_TYPE = re.compile(
    r"""\btype\s*=\s*(?:"\s*application/ld\+json\s*"|'\s*application/ld\+json\s*'|application/ld\+json\b)""", re.I
)


def description_text(posting):
    """The description as text."""
    markup = posting["description_html"]
    # Some sites escape the markup inside the JSON string (`&lt;p&gt;`).
    if "&lt;" in markup and "<" not in markup:
        return html_text(unescape_markup(markup))
    return html_text(markup)


def expired(posting, now):
    """Did the posting close before `now` (an aware datetime)? A date-time (with an offset, or
    without one read as UTC) is compared as an instant. A date without a time is compared with
    `now`'s UTC date and closes at the end of that day. A missing or unreadable date never expires."""
    raw = posting["valid_through"].strip()
    if not raw:
        return False
    moment = parse_datetime(raw)
    if moment is not None:
        return moment < now
    day = parse_date(raw)
    return day is not None and day < now.astimezone(datetime.timezone.utc).date()


def header_text(posting, skip=()):
    """The header lines of a posting: `Title`, `Company`, `Location`, `Remote`, `Employment type`,
    `Salary`, `Posted`, `Valid through`, only those the page gives, one per line."""
    lines = (
        ("Title", posting["title"]), ("Company", posting["company"]), ("Location", posting["location"]),
        ("Remote", "yes" if posting["remote"] else ""), ("Employment type", posting["employment_type"]),
        ("Salary", posting["salary"]), ("Posted", posting["date_posted"]), ("Valid through", posting["valid_through"]),
    )
    return "\n".join(f"{key}: {value}" for key, value in lines if value and key not in skip)


def with_header(head, body):
    """`head` and `body` as one text, a blank line between them (either may be empty)."""
    if not head:
        return body
    return f"{head}\n\n{body}" if body else head


def posting_text(posting):
    """The text of a posting built from its `JobPosting`: the header, a blank line, the description."""
    return with_header(header_text(posting), description_text(posting))


def job_postings(html, base):
    """Every `JobPosting` in the `application/ld+json` blocks of `html`, in page order, at most
    `MAX_POSTINGS`. A block may be one object, an array, an `@graph`, or an `ItemList` whose
    `itemListElement` entries hold the postings. Blocks that do not parse (common) or are larger
    than `MAX_BLOCK_BYTES` are skipped. `base` (a `Url`) resolves relative addresses."""
    found = []
    for attributes, block in _SCRIPT.findall(html):
        if len(found) >= MAX_POSTINGS:
            break
        if not _LD_TYPE.search(attributes) or len(block.encode("utf-8", "replace")) > MAX_BLOCK_BYTES:
            continue
        try:
            value = json.loads(_strip_wrappers(block))
        except (ValueError, RecursionError):
            continue
        items = []
        _collect(value, 0, items)
        found += [_posting(item, base) for item in items]
    return found[:MAX_POSTINGS]


def _strip_wrappers(block):
    """A block without the HTML comment or CDATA markers some pages wrap their JSON in."""
    text = block.strip()
    for opening, closing in (("<!--", "-->"), ("//<![CDATA[", "//]]>"), ("<![CDATA[", "]]>")):
        if text.startswith(opening):
            text = text[len(opening):]
            if text.endswith(closing):
                text = text[:-len(closing)]
            text = text.strip()
    return text


def _collect(value, depth, out):
    """The `JobPosting` objects inside `value`."""
    if depth > MAX_DEPTH or len(out) >= MAX_POSTINGS:
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, depth + 1, out)
    elif isinstance(value, dict):
        if _has_type(value, "JobPosting"):
            out.append(value)
            return
        if "@graph" in value:
            _collect(value["@graph"], depth + 1, out)
        if "itemListElement" in value:
            elements = value["itemListElement"]
            for element in elements if isinstance(elements, list) else [elements]:
                inner = element.get("item", element) if isinstance(element, dict) else element
                _collect(inner, depth + 1, out)


def _has_type(value, kind):
    """Is `@type` `kind`, or a list that holds it (with or without a schema.org prefix)?"""
    def named(text):
        return isinstance(text, str) and re.split(r"[/:]", text)[-1] == kind

    types = value.get("@type")
    return any(named(item) for item in types) if isinstance(types, list) else named(types)


def _str(value):
    return value if isinstance(value, str) else None


def _list(value):
    """A value that may be one item or a list of them."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _line(text):
    """A plain-text, one-line form of a page value (entities decoded, tags dropped)."""
    if not isinstance(text, str) or not text.strip():
        return ""
    return one_line(html_text(text) if "<" in text or "&" in text else text, FIELD_CHARS)


def _name(value):
    """A name: the string itself or its `name`."""
    if isinstance(value, dict):
        return _line(_str(value.get("name")))
    return _line(_str(value))


def _address_line(address):
    """"locality, region, country" of a `PostalAddress` (or an address given as a string)."""
    if isinstance(address, str):
        return _line(address)
    parts = []
    if isinstance(address, dict):
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            part = _name(address.get(key))
            if part and part not in parts:
                parts.append(part)
    return ", ".join(parts)


def _humanize(raw):
    """`FULL_TIME` -> "Full time"."""
    text = raw.strip().replace("_", " ").replace("-", " ").lower()
    return text[:1].upper() + text[1:]


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    if isinstance(value, str) and value.strip():
        return _line(value)
    return None


def _salary(amount):
    """`baseSalary` (a `MonetaryAmount`) as one line: "USD 100000-150000 per year"."""
    if not isinstance(amount, dict):
        return ""
    currency = _line(_str(amount.get("currency")))
    value, unit = amount.get("value"), ""
    if isinstance(value, dict):
        low, high, one = _number(value.get("minValue")), _number(value.get("maxValue")), _number(value.get("value"))
        if low is not None and high is not None and low != high:
            figure = f"{low}-{high}"
        else:
            figure = low or high or one or ""
        unit = _line(_str(value.get("unitText")))
    else:
        figure = _number(value) or ""
    if not figure:
        return ""
    out = f"{currency} {figure}" if currency else figure
    if unit:
        out += f" per {unit.lower()}"
    return one_line(out, FIELD_CHARS)


def _posting(item, base):
    url = None
    if isinstance(item.get("url"), str):
        joined = base.join(item["url"])
        if joined is not None and joined.is_http:
            url = joined.without_fragment()
    organization = item.get("hiringOrganization")
    company = _str(organization.get("name")) if isinstance(organization, dict) else _str(organization)
    places = []
    for place in _list(item.get("jobLocation")):
        line = _address_line(place.get("address", place) if isinstance(place, dict) else place)
        if line and line not in places:
            places.append(line)
    # A remote job often names where applicants may live instead.
    if not places:
        for area in _list(item.get("applicantLocationRequirements")):
            name = _name(area)
            if name and name not in places:
                places.append(name)
    kinds = [_humanize(kind) for kind in _list(item.get("employmentType")) if isinstance(kind, str)]
    return {
        # The posting's address resolved against the page (http or https); None when it has none.
        "url": url,
        "title": _line(_str(item.get("title")) or _str(item.get("name"))),
        "company": _line(company),
        # Each place as "locality, region, country", places joined by "; ".
        "location": _line("; ".join(places)),
        "remote": any(isinstance(kind, str) and kind.strip().upper() == "TELECOMMUTE" for kind in _list(item.get("jobLocationType"))),
        # The description as the page gives it (HTML; never shown as HTML).
        "description_html": _str(item.get("description")) or "",
        "date_posted": _line(_str(item.get("datePosted"))),
        "valid_through": _line(_str(item.get("validThrough"))),
        "employment_type": _line(", ".join(kind for kind in kinds if kind)),
        "salary": _salary(item.get("baseSalary")),
    }


_DATETIME = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)[Tt ](\d\d):(\d\d)(?::(\d\d)(\.\d+)?)?(Z|z|[+-]\d\d:?\d\d)?$"
)


def parse_datetime(text):
    """A date and time as an aware UTC datetime (an offset is honoured; none is read as UTC)."""
    found = _DATETIME.match(text.strip())
    if not found:
        return None
    year, month, day, hour, minute, second, fraction, zone = found.groups()
    try:
        moment = datetime.datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0),
            int(float(fraction) * 1_000_000) if fraction else 0, tzinfo=datetime.timezone.utc,
        )
    except ValueError:
        return None
    if zone and zone not in "Zz":
        digits = zone[1:].replace(":", "")
        offset = datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        moment = moment + offset if zone[0] == "-" else moment - offset
    return moment


def parse_date(text):
    """The day a date (or the start of a date-time) names: `2026-01-31`, `2026-01-31T...`."""
    try:
        return datetime.datetime.strptime(text.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
