"""Which items the agent reads. The counts have measured every item of the period; the agent reads
a capped pick, and it is the pick that its points can cite. So the pick is chosen to hold evidence
for what the counts found:

1. indicators, in order of importance: the skills whose direction is rising or falling, the skills
   of the gap, the terms not in the skill list, then the most mentioned skills. For each indicator
   in turn (round-robin) the newest item of the period that is not yet picked and names it, until
   about half of the cap is used or no indicator has an item left;
2. the rest: the other items round-robin over their sources, newest first, then the "Who is
   hiring" postings only in the room they leave (one Hacker News source holds thousands of them).

The indicator items come first: when the evidence of the brief is cut, the last items go. The pick
is a pure function of the stored items and the counts."""
from . import store, terms
from . import text as tx

TOP_SKILLS = 10


def indicators(counts, vocab):
    """The indicators of `counts`, most important first, each once: `("skill", index)` or `("term", text)`."""
    found = []

    def add(indicator):
        if indicator is not None and indicator not in found:
            found.append(indicator)

    def skill(name):
        places = vocab.find(name)
        return ("skill", places[0]) if places else None

    scopes = [counts.get("skills") or []] + ([counts["local"]["skills"]] if counts.get("local") else [])
    for scope in scopes:
        for moving in scope:
            if moving["direction"] in ("rising", "falling"):
                add(skill(moving["skill"]))
    for gap in counts.get("gap") or []:
        add(skill(gap["skill"]))
    for term in counts.get("uncatalogued") or []:
        text = tx.one_line(term["term"]).lower()
        add(("term", text) if text else None)
    for top in (counts.get("skills") or [])[:TOP_SKILLS]:
        add(skill(top["skill"]))
    return found


def named(text, stratum, vocab, found):
    """The indicators (by their place in `found`) that an item's own words name."""
    own = terms.body(text, stratum)
    skills, lower = vocab.skills_in(own), own.lower()
    return [at for at, (kind, value) in enumerate(found)
            if (value in skills if kind == "skill" else tx.mentions_lowered(lower, value))]


def spread(items, limit):
    """Take items round-robin over their sources (the input is newest first) until `limit`, then
    order the pick newest first. Items of a removed source count as one source."""
    if len(items) <= limit:
        return list(items)
    groups, index = [], {}
    for item in items:
        key = item.get("source_id")
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(item)
    picked, depth = [], 0
    while len(picked) < limit and any(depth < len(group) for group in groups):
        for group in groups:
            if len(picked) == limit:
                break
            if depth < len(group):
                picked.append(group[depth])
        depth += 1
    floor = tx.from_timestamp(0)
    picked.sort(key=lambda item: (store.moment_of(item) or floor, item["id"]), reverse=True)
    return picked


def choose(items, names, indicator_count, limit):
    """The pick, from the period's `items` (newest first, `(item, stratum)` pairs) and what each
    one names. The indicator part first, then the rest; at most `limit` in all."""
    queues = [[] for _ in range(indicator_count)]
    for at, found in enumerate(names[:len(items)]):
        for indicator in found:
            if indicator < indicator_count:
                queues[indicator].append(at)
    budget = (limit + 1) // 2
    taken, first = set(), []
    progress = True
    while progress and len(first) < budget:
        progress = False
        for queue in queues:
            if len(first) >= budget:
                break
            while queue and queue[0] in taken:
                queue.pop(0)
            if queue:
                at = queue.pop(0)
                taken.add(at)
                first.append(at)
                progress = True
    picked = [items[at][0] for at in first]
    rest = [pair for at, pair in enumerate(items) if at not in taken]
    others = [item for item, stratum in rest if stratum != "hiring_posting"]
    postings = [item for item, stratum in rest if stratum == "hiring_posting"]
    room = max(limit - len(picked), 0)
    others = spread(others, room)
    postings = spread(postings, max(room - len(others), 0))
    return picked + others + postings


def pick_items(period_items, vocab, counts, limit):
    """At most `limit` items of the period for the agent to read."""
    found = indicators(counts, vocab) if counts else []
    names = [named(item.get("text") or "", stratum, vocab, found) if found else [] for item, stratum in period_items]
    return choose(period_items, names, len(found), limit)
