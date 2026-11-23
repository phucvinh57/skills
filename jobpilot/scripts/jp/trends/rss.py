"""RSS 2.0 / RSS 1.0 (RDF) / Atom feeds the user adds (engineering blogs, newsletters).

Per entry the title, link, date and body. A feed that carries only teasers has the entry's page
fetched too, within the per-run cap and the per-host delay."""
import datetime
import html.entities
import re
import urllib.parse
import xml.etree.ElementTree as ElementTree

from .. import http as net
from . import config, page
from . import text as tx

# A feed body shorter than this is a teaser; the entry's own page is fetched instead.
TEASER_CHARS = 300
# Entries older than this before the period start are skipped (feeds keep years of posts).
BACKLOG_DAYS = 30
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


class NotFeed(Exception):
    """The address answered, but not with an RSS or Atom document."""


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _html_entities(xml):
    """`&nbsp;` and the other HTML names some feeds use without declaring them, as characters XML knows."""
    def swap(found):
        name = found.group(1)
        if name in _XML_ENTITIES:
            return found.group(0)
        code = html.entities.name2codepoint.get(name)
        return f"&#{code};" if code else f"&amp;{name};"

    return re.sub(r"&([A-Za-z][A-Za-z0-9]{1,30});", swap, xml)


def _entry(element):
    """One `<item>` / `<entry>` as `{title, link, published, body}`."""
    title, link, atom_link, guid, summary, content, published, updated = "", None, None, None, "", "", None, None
    for child in element:
        name = _local(child.tag)
        value = "".join(child.itertext()).strip()
        if name == "link":
            rel = child.get("rel")
            if atom_link is None and child.get("href") and rel in (None, "alternate"):
                atom_link = child.get("href")
            if value and link is None:
                link = value
        elif name == "title":
            title = tx.one_line(value)
        elif name in ("guid", "id") and value:
            guid = value
        elif name in ("description", "summary"):
            summary = value
        elif name in ("encoded", "content"):
            content = value
        elif name in ("pubDate", "published", "date"):
            published = published or tx.parse_date(value)
        elif name == "updated":
            updated = updated or tx.parse_date(value)
    # RSS: <link>; Atom: <link href> (alternate); else a guid that is itself an address.
    guid_link = guid if guid and guid.startswith(("http://", "https://")) else None
    return {"title": title, "link": link or atom_link or guid_link, "published": published or updated,
            "body": content or summary}


def parse_feed(xml):
    """The entries of an RSS or Atom document, in document order. Raises `NotFeed`."""
    try:
        root = ElementTree.fromstring(_html_entities(xml).encode("utf-8"))
    except ElementTree.ParseError:
        raise NotFeed("the address is not an RSS or Atom feed (it is not readable XML)")
    entries = [_entry(element) for element in root.iter() if _local(element.tag) in ("item", "entry")]
    if _local(root.tag) not in ("rss", "feed", "RDF") and not entries:
        raise NotFeed("the address is not an RSS or Atom feed")
    return entries


def read(feed_url):
    """`(address after redirects, entries)` of the feed at `feed_url`."""
    response = net.client().get(feed_url, ACCEPT)
    return response.url, parse_feed(response.text())


def collect(ctx, feed_url):
    """New entries of the feed as items, and how many new entries were cut: past `MAX_ITEMS` an
    entry is not read (its page is not fetched), only counted."""
    base, entries = read(feed_url)
    # Newest first (undated entries keep their place at the end).
    floor = datetime.datetime.min.replace(tzinfo=tx.UTC)
    entries.sort(key=lambda entry: (entry["published"] is not None, entry["published"] or floor), reverse=True)
    cutoff = ctx.period.start() - datetime.timedelta(days=BACKLOG_DAYS)
    items, seen, fetched, cut = [], set(), 0, 0
    for entry in entries:
        if entry["published"] is not None and entry["published"] < cutoff:
            continue
        url = tx.normalize_url(urllib.parse.urljoin(base, entry["link"])) if entry["link"] else None
        if url is None or url in ctx.known or url in seen:
            continue
        seen.add(url)
        if len(items) >= config.MAX_ITEMS:
            cut += 1
            continue
        body = tx.html_to_text(entry["body"])
        if len(body) < TEASER_CHARS and fetched < config.ARTICLE_FETCHES and _fetchable(url):
            fetched += 1
            try:
                body = page.fetch_text(url)[1]
            except (net.HttpError, page.Unreadable):
                pass  # the teaser is still a usable item
        if not body and not entry["title"]:
            continue
        items.append(page.make_item(url, entry["title"], entry["published"], tx.cap(body, config.MAX_TEXT_CHARS)))
    return items, cut


def _fetchable(url):
    """An address taken from third-party data is fetched only on a public host."""
    try:
        net.checked_url(url)
        return True
    except net.HttpError:
        return False
