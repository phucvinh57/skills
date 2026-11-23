"""Step 1 of a report: the agent reads the picked items in batches and writes, per item, topics
and claims with quotes. A claim whose quote is not in the item's stored text never gets past
`accept`. What was read is remembered per item (with a hash of its text), so a rebuild asks only
about new items."""
import hashlib

from .. import state
from ..cli import Fail
from . import cite, config, count, frame as frames, pick, store
from . import text as tx

MAX_TOPICS = 8
MAX_CLAIMS = 4
MAX_TOPIC_CHARS = 60
MAX_CLAIM_CHARS = 240
MAX_QUOTE_CHARS = 300

INSTRUCTIONS = (
    "Read each item and write, per item id, `topics` (up to 8 short lower-case labels of the skills, technologies, "
    "practices and business domains it is about; only what the item mentions) and `claims` (up to 4 concrete statements "
    "it makes about hiring, skills, funding, products, layoffs, adoption, prices or growth: `text` is your plain "
    "one-sentence summary, `quote` the exact words from the item that support it, copied character for character, at "
    "most 300 characters). A claim without an exact quote is thrown away, so skip it. For an item with a `Person:` line "
    "prefer what that person says about where technology, jobs, skills and the industry are going: `text` says who said "
    "it, `quote` is still the exact words from the item; words of someone else are not the person's. Never add anything "
    "that is not in the item. An item that says nothing concrete gets an empty `claims` list. Write one entry per item "
    "id shown, as JSON {\"<id>\": {\"topics\": [...], \"claims\": [{\"text\": ..., \"quote\": ...}]}}, then run "
    "`trends claims --file <that file>`."
)


def content_hash(item, person):
    """Hash of what an answer is about: a text saved again under the same address must not reuse an
    older answer, and an item read for a person is asked a different question."""
    digest = hashlib.sha256()
    digest.update((item.get("title") or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update((item.get("text") or "").encode("utf-8"))
    if person:
        digest.update(b"\0")
        digest.update(person.encode("utf-8"))
    return digest.hexdigest()


def digest_state(period):
    return store.load_state().get("digests", {}).get(period.label(), {})


def save_digest_state(period, **changes):
    saved = store.load_state()
    entry = saved.setdefault("digests", {}).setdefault(period.label(), {})
    entry.update(changes)
    store.save_state(saved)
    return entry


def picked(period, max_items=None):
    """`(counts, picked items, items by id, people by source)` for a report of `period`. The pick
    holds at most `max_items` items (the saved number, else the period's default)."""
    items = store.Items()
    frame, prefs = frames.load()
    counts = count.counts_for(period, items.items, frame, prefs)
    sources = store.load_sources()
    limit = max_items or digest_state(period).get("max_items") or config.digest_limits(period)[0]
    of_period = count.period_items(items.items, period, store.source_kinds(sources))
    chosen = pick.pick_items(of_period, count.Vocabulary(frame.skills), counts, limit)
    return counts, chosen, items, store.people(sources), frame, limit


def is_read(item, person, saved):
    entry = saved.get(str(item["id"]))
    return isinstance(entry, dict) and entry.get("hash") == content_hash(item, person)


def evidence(period, batch, offset, max_items=None):
    """One batch of the items that still have to be read, each inside `<item id="N">` markers."""
    _, chosen, _, people, _, limit = picked(period, max_items)
    if not chosen:
        raise Fail(f"there are no trend items for {period.label()} yet",
                   hint=f"collect first: `trends collect --period {period.label()}`")
    save_digest_state(period, items=[item["id"] for item in chosen], max_items=limit)
    saved = store.load_claims()
    pending = [item for item in chosen if not is_read(item, people.get(item.get("source_id")), saved)]
    shown = pending[offset:offset + max(batch, 1)]
    blocks = []
    for item in shown:
        person = people.get(item.get("source_id"))
        chars = config.ITEM_CHARS * (config.PERSON_ITEM_FACTOR if person else 1)
        head = (f"Person: {tx.one_line(person)}\n" if person else "") + f"Title: {tx.one_line(item.get('title') or '')}\nText:\n"
        blocks.append(state.fence("item", head + tx.cap(item.get("text") or "", chars), id=item["id"]))
    return {
        "period": period.label(),
        "picked": len(chosen),
        "pending": len(pending),
        "offset": offset,
        "shown": [item["id"] for item in shown],
        "note": state.UNTRUSTED_NOTE,
        "instructions": (INSTRUCTIONS if shown else "Every picked item has been read. Next: `trends brief`." if not pending
                         else "Nothing at this offset: run `trends evidence` without --offset."),
        "items": "\n\n".join(blocks),
        "next": (f"after `trends claims`, run `trends evidence --period {period.label()}` again until `pending` is 0"
                 if pending else f"trends brief --period {period.label()}"),
    }


def _entries(answer):
    """The answer as `(id, entry)` pairs: `{id: {...}}`, or `{"items": [{id, ...}]}`."""
    if isinstance(answer, dict) and isinstance(answer.get("items"), list):
        answer = answer["items"]
    if isinstance(answer, list):
        return [(entry.get("id"), entry) for entry in answer if isinstance(entry, dict)]
    if isinstance(answer, dict):
        return [(key, entry) for key, entry in answer.items() if isinstance(entry, dict)]
    raise Fail("the file is not a JSON object of item ids", hint='write {"<id>": {"topics": [...], "claims": [...]}}')


def verify(item, entry):
    """What an answer about `item` may keep: `(topics, claims, dropped claims with the reason)`."""
    haystack = cite.haystack(f"{item.get('title') or ''}\n{item.get('text') or ''}")
    topics = []
    for topic in entry.get("topics") if isinstance(entry.get("topics"), list) else []:
        if not isinstance(topic, str):
            continue
        topic = tx.truncate(tx.one_line(topic), MAX_TOPIC_CHARS)
        if topic and topic.lower() not in (known.lower() for known in topics) and len(topics) < MAX_TOPICS:
            topics.append(topic)
    kept, dropped = [], []
    for claim in entry.get("claims") if isinstance(entry.get("claims"), list) else []:
        if not isinstance(claim, dict):
            continue
        text = tx.truncate(tx.one_line(claim.get("text") if isinstance(claim.get("text"), str) else ""), MAX_CLAIM_CHARS)
        quote = tx.one_line(claim.get("quote") if isinstance(claim.get("quote"), str) else "")
        if not text:
            continue
        if len(kept) >= MAX_CLAIMS:
            dropped.append({"id": item["id"], "text": text, "reason": f"more than {MAX_CLAIMS} claims for one item"})
        elif len(quote) > MAX_QUOTE_CHARS:
            dropped.append({"id": item["id"], "text": text, "reason": f"the quote is longer than {MAX_QUOTE_CHARS} characters"})
        elif not cite.quote_found(quote, haystack):
            dropped.append({"id": item["id"], "text": text, "reason": "the quote is not in the item's text"})
        else:
            kept.append({"text": text, "quote": quote})
    return topics, kept, dropped


def accept(answer):
    """Check the agent's answer against the stored items and save what holds."""
    items = store.Items()
    people = store.people()
    saved = store.load_claims()
    accepted, dropped, unknown, seen = 0, [], [], set()
    for raw_id, entry in _entries(answer):
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            unknown.append(str(raw_id))
            continue
        item = items.get(item_id)
        if item is None or item_id in seen:
            unknown.append(str(raw_id))
            continue
        seen.add(item_id)
        person = people.get(item.get("source_id"))
        topics, kept, lost = verify(item, entry)
        dropped.extend(lost)
        saved[str(item_id)] = {"hash": content_hash(item, person), "topics": topics, "claims": kept}
        accepted += 1
    state.write_json(store.CLAIMS, saved)
    return {"items_recorded": accepted, "claims_kept": sum(len(saved[str(i)]["claims"]) for i in seen),
            "claims_dropped": dropped, "unknown_ids": unknown,
            "next": "run `trends evidence` again until `pending` is 0, then `trends brief`"}


def extractions(chosen, people):
    """The saved answers that still fit their item, by item id."""
    saved = store.load_claims()
    return {item["id"]: saved[str(item["id"])] for item in chosen if is_read(item, people.get(item.get("source_id")), saved)}
