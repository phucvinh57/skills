"""What the agent wrote is checked in code before it is shown, then saved as the period's report.

A point needs a citation that is an item of this report's brief; a quote must be found in a cited
item (a cited item's whole title also counts). A point must agree with what the code counted: a
rising or falling skill must not contradict the code's direction, a `for_you` point is about a
skill of the gap, a `wishes_check` point about one of the user's wishes. An `outlook` point is an
opinion, so it is tied to who said it: its topic is a person the user follows, it cites items of
that person's source only, and it carries a quote found in one of them."""
from .. import state
from ..cli import Fail
from . import claims, config, count, report, store
from . import period as clock
from . import text as tx

# A quote shorter than this (after `normalize`) proves nothing: one word is in every text.
MIN_QUOTE_CHARS = 10
MAX_CITATIONS = 5
MAX_POINTS_PER_SECTION = 4
MAX_POINTS = 20
# Points read from one answer (before the real limits apply).
MAX_POINTS_READ = 3 * MAX_POINTS
MAX_TEXT_CHARS = 500
MAX_TOPIC_CHARS = 60
MAX_QUOTE_CHARS = 300

SECTIONS = config.SECTIONS


def normalize(text):
    """Lower case, every run of non-alphanumeric characters one space: quotes survive changes of
    quote marks, dashes, line breaks and case, nothing else."""
    out, gap = [], True
    for char in text:
        if char.isalnum():
            out.append(char.lower())
            gap = False
        elif not gap:
            out.append(" ")
            gap = True
    return "".join(out).rstrip(" ")


def haystack(text):
    """The text a quote is searched in: normalised and padded, so a match starts and ends at a word."""
    return f" {normalize(text)} "


def quote_found(quote, hay):
    quote = normalize(quote)
    return len(quote) >= MIN_QUOTE_CHARS and f" {quote} " in hay


def item_has_quote(item, quote):
    """The quote is in the item's title or text, or is the item's whole title (however short)."""
    wanted = normalize(quote)
    title = item.get("title") or ""
    return (bool(wanted) and wanted == normalize(title)) or quote_found(quote, haystack(f"{title}\n{item.get('text') or ''}"))


def key(text):
    return tx.one_line(text).lower()


class CodeFacts:
    """What the code counted that a point must not contradict. Topics are matched ignoring case,
    and a skill also by the name the vocabulary gives it ("golang" is Go)."""

    def __init__(self, counts):
        scopes = [counts.get("skills") or []] + ([counts["local"]["skills"]] if counts.get("local") else [])
        self.directions = [{key(skill["skill"]): skill["direction"] for skill in scope} for scope in scopes]
        self.gap = {key(skill["skill"]) for skill in counts.get("gap") or []}
        self.wishes = {key(wish["wish"]) for wish in counts.get("wishes") or []}
        self.vocab = count.Vocabulary()

    def keys(self, topic):
        name = self.vocab.canonical(topic)
        return [key(topic)] + ([key(name)] if name else [])

    def directions_of(self, topic):
        keys = self.keys(topic)
        found = []
        for scope in self.directions:
            direction = next((scope[k] for k in keys if k in scope), None)
            if direction:
                found.append(direction)
        return found

    def contradicts(self, topic, claimed):
        """Every scope that counted the skill gives it a clear direction other than `claimed`. A
        skill that was not counted, or is unclear or `claimed` in one scope, keeps its point."""
        found = self.directions_of(topic)
        return bool(found) and all(direction not in (claimed, "unclear") for direction in found)

    def disagreement(self, point):
        """Why the point is against the counts; None when it agrees."""
        topic, section = point["topic"], point["section"]
        for claimed, name in (("rising", "rising_skills"), ("falling", "falling_skills")):
            if section == name and self.contradicts(topic, claimed):
                says = " / ".join(self.directions_of(topic))
                return f"contradicts the code's direction for {topic}: the code says {says}"
        if section == "for_you" and topic and not any(k in self.gap for k in self.keys(topic)):
            return "the topic is not a skill of the gap list"
        if section == "wishes_check" and key(topic) not in self.wishes:
            return "the topic is not one of the wishes"
        return None


def parse_points(answer):
    """The agent's points. A point with an unknown section or without text is reported; citations
    that are not whole numbers are ignored."""
    points = answer.get("points") if isinstance(answer, dict) else answer
    if not isinstance(points, list):
        raise Fail("the file has no `points` list", hint='write {"points": [{"section": ..., "topic": ..., "text": ..., "quote": ..., "citations": [...]}]}')
    text = lambda entry, name, limit: tx.truncate(tx.one_line(entry.get(name) if isinstance(entry.get(name), str) else ""), limit)  # noqa: E731
    parsed, dropped = [], []
    for entry in points:
        if not isinstance(entry, dict):
            continue
        point = {"section": entry.get("section"), "topic": text(entry, "topic", MAX_TOPIC_CHARS),
                 "text": text(entry, "text", MAX_TEXT_CHARS), "quote": text(entry, "quote", MAX_QUOTE_CHARS),
                 "citations": [n for n in entry.get("citations") or [] if isinstance(n, int) and not isinstance(n, bool)]
                 if isinstance(entry.get("citations"), list) else []}
        if point["section"] not in SECTIONS:
            dropped.append(_dropped(point, f"unknown section (use one of: {', '.join(SECTIONS)})"))
        elif not point["text"]:
            dropped.append(_dropped(point, "the point has no text"))
        elif len(parsed) >= MAX_POINTS_READ:
            dropped.append(_dropped(point, f"more than {MAX_POINTS_READ} points in one answer"))
        else:
            parsed.append(point)
    return parsed, dropped


def _dropped(point, reason):
    return {"section": point.get("section") if isinstance(point.get("section"), str) else "", "topic": point.get("topic", ""),
            "text": point.get("text", ""), "reason": reason}


def outlook_citations(people, items, point, ids):
    """The citations an `outlook` point may keep: the items (of `ids`) of the followed person its
    topic names, with that person's name as the user wrote it; or the reason it has none."""
    wanted = key(point["topic"])
    if not wanted:
        return None, "an outlook point needs the followed person's name as its topic"
    if not point["quote"]:
        return None, "an outlook point needs a quote from the person's item"
    name, own = None, []
    for item_id in ids:
        item = items.get(item_id)
        person = people.get(item.get("source_id")) if item else None
        if person and key(person) == wanted:
            name = person
            own.append(item_id)
    if name is None:
        if wanted not in (key(person) for person in people.values()):
            return None, "the topic is not a person the user follows"
        return None, "none of the cited items is that person's (it cites another person's or another source's item)"
    return (name, own), None


def enforce(points, valid, counts, people, items):
    """`(kept points in section order, dropped points each with its reason)`.

    Dropped: a point against the counts, a point without a valid citation (an id of an item the
    brief showed), an `outlook` point not tied to a followed person, a point whose quote is in none
    of its cited items, a repeated point, and the points past the limits (the first
    `MAX_POINTS_PER_SECTION` of a section and the first `MAX_POINTS` in all stay). Invalid ids are
    removed from the points that stay; at most `MAX_CITATIONS` are kept."""
    facts = CodeFacts(counts)
    kept, dropped = [], []
    for point in points:
        point = dict(point, text=tx.one_line(point["text"]), topic=tx.one_line(point["topic"]), quote=tx.one_line(point["quote"]))
        if not point["text"]:
            dropped.append(_dropped(point, "the point has no text"))
            continue
        against = facts.disagreement(point)
        if against:
            dropped.append(_dropped(point, against))
            continue
        ids = []
        for item_id in point["citations"]:
            if item_id in valid and item_id not in ids and len(ids) < MAX_CITATIONS and items.get(item_id):
                ids.append(item_id)
        if not ids:
            dropped.append(_dropped(point, "no valid citation: cite the id of an item shown in the brief"))
            continue
        if point["section"] == "outlook":
            found, reason = outlook_citations(people, items, point, ids)
            if found is None:
                dropped.append(_dropped(point, reason))
                continue
            point["topic"], ids = found
        if point["quote"] and not any(item_has_quote(items.get(item_id), point["quote"]) for item_id in ids):
            dropped.append(_dropped(point, "the quote is not found in a cited item"))
            continue
        if any(other["section"] == point["section"] and other["text"] == point["text"] for other in kept):
            dropped.append(_dropped(point, "the same point twice"))
            continue
        if len(kept) >= MAX_POINTS:
            dropped.append(_dropped(point, f"over the limit: at most {MAX_POINTS} points in total"))
            continue
        if len([1 for other in kept if other["section"] == point["section"]]) >= MAX_POINTS_PER_SECTION:
            dropped.append(_dropped(point, f"over the limit: at most {MAX_POINTS_PER_SECTION} points per section"))
            continue
        point["citations"] = ids
        kept.append(point)
    kept.sort(key=lambda point: SECTIONS.index(point["section"]))
    return kept, dropped


# The reasons a reader of the report is told about (a point over a limit or said twice is not "left out").
_QUIET = ("over the limit", "the same point twice")


def check(period, answer):
    """Check the agent's points against what the brief showed and save the report (a rerun
    replaces it)."""
    saved = claims.digest_state(period)
    if not isinstance(saved.get("counts"), dict) or not isinstance(saved.get("shown"), list):
        raise Fail(f"there is no brief for {period.label()} yet", hint=f"run `trends brief --period {period.label()}` first")
    points, unreadable = parse_points(answer)
    items = store.Items()
    kept, dropped = enforce(points, set(saved["shown"]), saved["counts"], store.people(), items)
    dropped = unreadable + dropped

    cited = []
    for point in kept:
        for item_id in point["citations"]:
            item = items.get(item_id)
            if item_id not in [entry["id"] for entry in cited]:
                cited.append({"id": item_id, "title": item.get("title") or "", "url": item["url"]})
    data = {
        "items": cited,
        "points": kept,
        "counts": saved["counts"],
        "items_read": len(saved.get("items") or []),
        "dropped": len([1 for entry in dropped if not entry["reason"].startswith(_QUIET)]),
    }
    body = {"period": period.label(), "kind": period.kind(), "title": clock.report_title(period),
            "created_at": tx.iso(clock.now()), "data": data}
    label = period.label()
    state.write_json(store.report_path(label, "json"), body)
    state.write_text(store.report_path(label, "md"), report.render_markdown(period, data))
    return {
        "period": label,
        "saved": state.path(store.report_path(label, "json")),
        "markdown": state.path(store.report_path(label, "md")),
        "points_kept": len(kept),
        "points_dropped": dropped,
        "sections": {section: len([1 for point in kept if point["section"] == section]) for section in SECTIONS
                     if any(point["section"] == section for point in kept)},
        "next": f"`trends show --period {label}` prints the report; `trends render --period {label}` makes the PDF. "
                "Do not rewrite a dropped point to get it past the check unless the evidence really supports it.",
    }
