"""A careers site's `sitemap.xml`: when neither the careers page nor its JSON-LD gives postings,
the posting-shaped addresses in the sitemap are the next thing to try.

Code picks every address: the sitemap is always `<scheme>://<configured host>/sitemap.xml`; of a
sitemap index only a few child sitemaps on the same site whose address names jobs are read; and a
`<loc>` is a candidate only when it passes `same_site` and `looks_like_posting`, like a link on the
page. A missing or broken sitemap is not an error: it gives no candidates.
"""
import re

from ..cli import Fail
from .base import XmlError, candidate, local_name, parse_url, xml_events
from .jsonld import parse_date, parse_datetime
from .web import JOB_WORDS, looks_like_posting, page_url, plain, same_site, title_from_url

# Most child sitemaps of an index that are read.
MAX_CHILDREN = 3
# Most `<loc>` entries kept from one file (the sitemap protocol's own limit).
MAX_ENTRIES = 50_000


def parse(xml):
    """`(is_index, entries)` of a `<urlset>` or a `<sitemapindex>`; an entry is `(loc, lastmod)`,
    `lastmod` `""` when absent. `XmlError` for anything else or for broken XML.

    The root is depth 1, an entry (`<url>`, `<sitemap>`) 2, its own `<loc>` and `<lastmod>` 3.
    Deeper ones (`<image:image><image:loc>` in WordPress sitemaps) are other things' addresses."""
    is_index, entries = None, []
    for event, element, depth in xml_events(xml):
        name = local_name(element.tag)
        if event == "start":
            if depth == 1:
                if name not in ("urlset", "sitemapindex"):
                    raise XmlError(f"not a sitemap (root element `{name}`)")
                is_index = name == "sitemapindex"
        elif depth == 2 and name in ("url", "sitemap"):
            loc = lastmod = ""
            for child in element:
                field = local_name(child.tag)
                if field == "loc":
                    loc = (child.text or "").strip()
                elif field == "lastmod":
                    lastmod = (child.text or "").strip()
            if loc and len(entries) < MAX_ENTRIES:
                entries.append((loc, lastmod))
            element.clear()
    if is_index is None:
        raise XmlError("not a sitemap")
    return is_index, entries


def sitemap_url(site):
    """The sitemap address for the configured `site`: its scheme and host (and port), `/sitemap.xml`."""
    return site.replace(path="/sitemap.xml", query=None, fragment=None)


def read_sitemap(net, site, listing, company, limit):
    """The postings the sitemap of the configured `site` lists, newest `<lastmod>` first, at most
    `limit`. `listing` is the careers page itself, which is not a posting. Never an error: a
    missing, walled or broken sitemap gives no candidates."""
    first = _read_file(net, site, sitemap_url(site))
    if first is None:
        return []
    is_index, entries = first
    if is_index:
        children, entries = entries, []
        for child in _job_children(site, children):
            read = _read_file(net, site, child)
            if read is not None and not read[0]:
                entries += read[1]
    # Newest first; entries without a readable date keep their order, after the dated ones.
    dated = [(_lastmod(lastmod), loc) for loc, lastmod in entries]
    dated.sort(key=lambda pair: (0, -pair[0].timestamp()) if pair[0] is not None else (1, 0))
    seen, found = set(), []
    for _, loc in dated:
        if len(found) >= limit:
            break
        url = parse_url(loc)
        if url is None or not url.is_http:
            continue
        url = url.without_fragment()
        if not looks_like_posting(site, listing, url) or str(url) in seen:
            continue
        seen.add(str(url))
        title = title_from_url(url)
        if title is None:
            continue
        found.append(candidate(str(url), title, company))
    return found


def _job_children(site, children):
    """Up to `MAX_CHILDREN` child sitemaps of an index: on the same site, named for jobs, not gzip."""
    out = []
    for loc, _ in children:
        url = parse_url(loc.strip())
        if url is None or not url.is_http or not same_site(site, url):
            continue
        path = url.path.lower()
        if path.endswith(".gz") or not any(word in JOB_WORDS for word in re.split(r"[^a-z0-9]", path)):
            continue
        out.append(url)
        if len(out) >= MAX_CHILDREN:
            break
    return out


def _read_file(net, site, url):
    """One sitemap file read plain; None when it cannot be read, landed on another site, or is not
    a sitemap."""
    try:
        page = plain(net, str(url))
    except Fail:
        return None
    landed = page_url(page)
    if landed is None or not same_site(site, landed):
        return None
    try:
        return parse(page.html)
    except XmlError:
        return None


def _lastmod(text):
    moment = parse_datetime(text)
    if moment is not None:
        return moment
    day = parse_date(text)
    return parse_datetime(f"{day.isoformat()}T00:00:00") if day is not None else None
