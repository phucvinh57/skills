"""What the trends code keeps in the data folder:

    trends/sources.json   [{id, kind: hackernews|yc|rss|page|person, name, url, enabled}]
    trends/items.json     [{id, source_id, url, title, published_at, fetched_at, text, person}]
    trends/claims.json    {"<item id>": {hash, topics[], claims[{text, quote}]}}
    trends/state.json     settled hiring threads, the last run and the pick per period
    trends/reports/<period>.json + .md (+ .pdf / .html)

Item ids are stable (never reused); items are deduplicated by their normalised address.
"""
from .. import state
from ..cli import Fail
from . import period as clock
from . import text as tx

KINDS = ("hackernews", "yc", "rss", "page", "person")
LABELS = {"hackernews": "Hacker News", "yc": "YC companies", "rss": "RSS / Atom feed", "page": "Web page",
          "person": "Person to follow"}
MAX_PERSON_NAME_CHARS = 60

SOURCES = "trends/sources.json"
ITEMS = "trends/items.json"
CLAIMS = "trends/claims.json"
STATE = "trends/state.json"


def _defaults():
    return [
        {"id": 1, "kind": "hackernews", "name": LABELS["hackernews"], "url": "", "enabled": True},
        {"id": 2, "kind": "yc", "name": LABELS["yc"], "url": "", "enabled": True},
    ]


def load_sources():
    """The trend sources; Hacker News and the YC directory exist until the user removes them."""
    sources = state.read_json(SOURCES)
    if sources is None:
        sources = _defaults()
        state.write_json(SOURCES, sources)
    return [source for source in sources if isinstance(source, dict) and source.get("kind") in KINDS]


def save_sources(sources):
    state.write_json(SOURCES, sources)


def load_state():
    value = state.read_json(STATE, {})
    return value if isinstance(value, dict) else {}


def save_state(value):
    state.write_json(STATE, value)


def people(sources=None):
    """The person each `person` source follows, by source id. An item of a source that is gone has
    no person."""
    sources = load_sources() if sources is None else sources
    return {source["id"]: source["name"] for source in sources
            if source["kind"] == "person" and (source.get("name") or "").strip()}


def source_kinds(sources=None):
    sources = load_sources() if sources is None else sources
    return {source["id"]: source["kind"] for source in sources}


class Items:
    """`trends/items.json`, loaded once per command."""

    def __init__(self):
        loaded = state.read_json(ITEMS, [])
        self.items = [item for item in loaded if isinstance(item, dict) and "id" in item and item.get("url")]
        self.by_url = {item["url"]: item for item in self.items}
        self.by_id = {item["id"]: item for item in self.items}
        saved = load_state().get("next_item_id", 1)
        self.next_id = max([saved] + [item["id"] + 1 for item in self.items])

    def save(self):
        state.write_json(ITEMS, self.items)
        current = load_state()
        if current.get("next_item_id") != self.next_id:
            current["next_item_id"] = self.next_id
            save_state(current)

    def get(self, item_id):
        return self.by_id.get(item_id)

    def urls(self):
        return set(self.by_url)

    def person_urls(self, people_by_source):
        """The addresses of the items collected by the sources of followed people."""
        return {item["url"] for item in self.items if item.get("source_id") in people_by_source}

    def add(self, source, found, people_by_source=()):
        """Store an item of `source` unless its address is known: True when it is new. An item a
        person's source read that is stored already goes to that source (with the longer of the two
        texts, and now as the time it was fetched); it still counts as a duplicate."""
        person = source["name"] if source["kind"] == "person" else None
        known = self.by_url.get(found["url"])
        stamp = tx.iso(clock.now())
        if known is None:
            item = {
                "id": self.next_id, "source_id": source["id"], "url": found["url"], "title": found["title"],
                "published_at": tx.iso(found.get("published_at")), "fetched_at": stamp,
                "text": found["text"], "person": person,
            }
            self.next_id += 1
            self.items.append(item)
            self.by_url[item["url"]] = item
            self.by_id[item["id"]] = item
            return True
        if person and known.get("source_id") not in people_by_source:
            known["source_id"], known["person"], known["fetched_at"] = source["id"], person, stamp
            if len(found["text"]) > len(known.get("text") or ""):
                known["text"] = found["text"]
        return False


def moment_of(item):
    """When an item is from: published, else fetched."""
    return tx.from_iso(item.get("published_at")) or tx.from_iso(item.get("fetched_at"))


def load_claims():
    value = state.read_json(CLAIMS, {})
    return value if isinstance(value, dict) else {}


def report_path(label, extension):
    return f"trends/reports/{label}.{extension}"


def load_report(period):
    report = state.read_json(report_path(period.label(), "json"))
    if not report:
        raise Fail(f"there is no report for {period.label()} yet",
                   hint="run `trends brief` and `trends check` first (references/trends.md)")
    return report
