"""What the market asks for, per month, counted in code.

The market is the "Who is hiring" postings among the collected items: nobody filtered them for the
user. A document counts once for a skill however often it names it, and a skill's weight in a
month is its share of the documents read that month, so a busy month does not look like a trend.
The vocabulary is fixed (`BUILT_IN`) plus the skills of the user's profile, so the same postings
and the same profile always give the same numbers.

- global: every posting of the window; local: the postings of the user's place, when there is one;
- a direction per skill and scope (`judge`): the period's own months against the months before
  them (a step) and the trend over every month, all of a scope's tests under one control of false
  discoveries; `unclear` when the data cannot tell;
- the gap (`gap_bucket`): the skills of the postings in the user's field, sorted by direction
  against "in the profile"; judged on the local scope when each side of its window has enough
  postings, else on the global one;
- terms not in the list (`terms`), and coverage: how much was read and what was not seen;
- wishes: each wished or avoided skill and domain, measured in the gap's scope. They are never
  part of the market vocabulary, so they cannot change a market number, a direction or the gap."""
import datetime
import re

from . import stats, store, terms
from . import period as clock
from . import text as tx

BASELINE_MONTHS = 5           # months before a week's or a month's own month
QUARTER_BASELINE_MONTHS = 3   # months before a quarter's own months
MAX_SKILLS = 30               # skills kept in a scope's list
HN_ITEM_PREFIX = "https://news.ycombinator.com/item?id="
STEP_PRIOR = 1.0
HIGH_SHARE_PERMILLE = 50      # share of the field's postings from which a skill is "often asked for"
MAX_PER_BUCKET = 8
CURRENT_THREAD_DAYS = 31      # how old an HN item fetched in the period may be and still count as the period's
MARKET_SCAN_VERSION = 1

STRATA = ("hiring_posting", "hn_story", "yc", "feed", "other")
STRATUM_PLURAL = {"hiring_posting": "hiring postings", "hn_story": "Hacker News stories", "yc": "YC companies",
                  "feed": "feed and page items", "other": "other items"}

# `(name, aliases)`. An alias is matched as whole words, ignoring case; one that starts with `=`
# must match exactly as written (`=Go`: the language, not the verb).
BUILT_IN = (
    ('Rust', ('rust',)),
    ('Go', ('=Go', 'golang')),
    ('Python', ('python',)),
    ('Java', ('java',)),
    ('Kotlin', ('kotlin',)),
    ('Scala', ('scala',)),
    ('TypeScript', ('typescript',)),
    ('JavaScript', ('javascript',)),
    ('C++', ('c++',)),
    ('C#', ('c#',)),
    ('Swift', ('=Swift',)),
    ('Ruby', ('ruby',)),
    ('PHP', ('php',)),
    ('Elixir', ('elixir',)),
    ('Erlang', ('erlang',)),
    ('Haskell', ('haskell',)),
    ('Clojure', ('clojure',)),
    ('OCaml', ('ocaml',)),
    ('Zig', ('=Zig',)),
    ('Dart', ('=Dart',)),
    ('SQL', ('sql',)),
    ('React', ('react', 'react.js', 'reactjs')),
    ('React Native', ('react native',)),
    ('Vue', ('vue', 'vue.js', 'vuejs')),
    ('Angular', ('angular',)),
    ('Svelte', ('svelte',)),
    ('Next.js', ('next.js', 'nextjs')),
    ('Node.js', ('node.js', 'nodejs')),
    ('Django', ('django',)),
    ('Flask', ('flask',)),
    ('FastAPI', ('fastapi',)),
    ('Spring', ('=Spring',)),
    ('Rails', ('rails',)),
    ('Laravel', ('laravel',)),
    ('.NET', ('.net', 'asp.net', 'dotnet')),
    ('GraphQL', ('graphql',)),
    ('gRPC', ('grpc',)),
    ('Flutter', ('flutter',)),
    ('Android', ('android',)),
    ('iOS', ('ios',)),
    ('PostgreSQL', ('postgres', 'postgresql')),
    ('MySQL', ('mysql',)),
    ('MongoDB', ('mongodb', 'mongo')),
    ('Redis', ('redis',)),
    ('Kafka', ('kafka',)),
    ('Spark', ('=Spark', 'pyspark')),
    ('Flink', ('flink',)),
    ('Snowflake', ('snowflake',)),
    ('BigQuery', ('bigquery',)),
    ('Databricks', ('databricks',)),
    ('dbt', ('dbt',)),
    ('Airflow', ('airflow',)),
    ('Elasticsearch', ('elasticsearch', 'opensearch')),
    ('ClickHouse', ('clickhouse',)),
    ('Cassandra', ('cassandra',)),
    ('DynamoDB', ('dynamodb',)),
    ('SQLite', ('sqlite',)),
    ('AWS', ('aws',)),
    ('GCP', ('gcp', 'google cloud')),
    ('Azure', ('azure',)),
    ('Kubernetes', ('kubernetes', 'k8s')),
    ('Docker', ('docker',)),
    ('Terraform', ('terraform',)),
    ('Ansible', ('ansible',)),
    ('Helm', ('=Helm',)),
    ('Linux', ('linux',)),
    ('Prometheus', ('prometheus',)),
    ('Grafana', ('grafana',)),
    ('GitHub Actions', ('github actions',)),
    ('Nix', ('=Nix', 'nixos')),
    ('Machine Learning', ('machine learning',)),
    ('PyTorch', ('pytorch',)),
    ('TensorFlow', ('tensorflow',)),
    ('LLM', ('llm', 'llms')),
    ('Generative AI', ('generative ai', 'genai')),
    ('RAG', ('=RAG',)),
    ('LangChain', ('langchain',)),
    ('Computer Vision', ('computer vision',)),
    ('NLP', ('=NLP', 'natural language processing')),
    ('WebAssembly', ('wasm', 'webassembly')),
    ('Solidity', ('solidity',)),
    ('Blockchain', ('blockchain',)),
    ('Microservices', ('microservice', 'microservices')),
    ('Distributed Systems', ('distributed systems',)),
    ('Observability', ('observability',)),
)

_WORD = re.compile(r"\.net\b|[a-z0-9][a-z0-9+#.\-]*", re.I)


def tokens(text):
    """`(as written, lower case)` per word: runs of letters and digits that may contain `+ # . -`
    (`c++`, `node.js`, `front-end`), or `.NET`. Trailing dots and dashes are cut."""
    out = []
    for found in _WORD.finditer(text):
        word = found.group(0).rstrip(".-")
        if word:
            out.append((word, word.lower()))
    return out


class Vocabulary:
    """The skills that are counted and the words that stand for them: the built-in skills plus
    `extra` (the user's skills). An extra skill the vocabulary already knows adds nothing ("golang"
    in a profile stands for the built-in Go). An extra skill of one or two letters or digits ("C",
    "R") is not counted: it would match "Series C" or "R&D" in every other posting."""

    def __init__(self, extra=()):
        self.names = []
        self.by_first_word = {}
        for name, aliases in BUILT_IN:
            self._add(name, aliases)
        for skill in extra:
            skill = tx.one_line(skill)
            ambiguous = len(skill) <= 2 and skill.isalnum()
            if skill and not ambiguous and not self.find(skill):
                self._add(skill, (skill.lower(),))

    def _add(self, name, aliases):
        if name.lower() in (known.lower() for known in self.names):
            return
        skill = len(self.names)
        for alias in aliases:
            exact = alias.startswith("=")
            words = tokens(alias[1:] if exact else alias)
            if not words:
                continue
            written, first = words[0]
            self.by_first_word.setdefault(first, []).append((skill, [lower for _, lower in words[1:]], written if exact else None))
        self.names.append(name)

    def _matches(self, text, strict):
        words = tokens(text)
        found = set()
        for at, (written, lower) in enumerate(words):
            for skill, rest, exact in self.by_first_word.get(lower, ()):
                if strict and exact is not None and exact != written:
                    continue
                if all(at + 1 + k < len(words) and words[at + 1 + k][1] == word for k, word in enumerate(rest)):
                    found.add(skill)
        return found

    def skills_in(self, text):
        """The skills a posting names (an alias that must match case does: "we go to market" is not Go)."""
        return self._matches(text, True)

    def find(self, label):
        """The skills a short label stands for (a profile skill, a wish, a topic), the skill of that
        name first: "Golang" is Go, "Spring Boot" is Spring."""
        label = label.strip()
        found = [at for at, name in enumerate(self.names) if name.lower() == label.lower()][:1]
        for skill in sorted(self._matches(label, False)):
            if skill not in found:
                found.append(skill)
        return found

    def canonical(self, label):
        found = self.find(label)
        return self.names[found[0]] if found else None

    def find_all(self, labels):
        return {skill for label in labels for skill in self.find(label)}


class Doc:
    """One counted document: the month it belongs to (`2026-11`) and its text."""

    def __init__(self, month, text):
        self.month, self.text, self.lower = month, text, text.lower()


# --- the window ----------------------------------------------------------------------------------

def month_index(date):
    return date.year * 12 + date.month - 1


def month_label(index):
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_ending(last, count):
    return [month_label(last - back) for back in range(count - 1, -1, -1)]


def last_month(period):
    return month_index((period.end() - datetime.timedelta(days=1)).date())


def month_window(period, count):
    """`count` months ending with the month of the period's last day, oldest first."""
    return months_ending(last_month(period), count)


def window_for(period, today):
    """`(months, target)`: the months a report of `period` built on `today` counts, oldest first,
    and how many of the last are the period's own (the others are the baseline). It ends with the
    period's last month, but never after today's.

    - week, month: the target is the one month the period ends in, the baseline the 5 before;
    - quarter: the target is the quarter's months that have begun, the baseline the 3 before;
    - year: the target is the year's months that have begun, the baseline as many before."""
    first = month_index(period.start().date())
    end = max(min(last_month(period), month_index(today)), first)
    begun = end - first + 1
    if period.unit in ("week", "month"):
        target, baseline = 1, BASELINE_MONTHS
    elif period.unit == "quarter":
        target, baseline = begun, QUARTER_BASELINE_MONTHS
    else:
        target, baseline = begun, begun
    return months_ending(end, baseline + target), target


# --- direction -----------------------------------------------------------------------------------

def sides(values, target):
    """The sums of the two sides of a row: the baseline, then the target (the last `target`)."""
    cut = max(len(values) - target, 0)
    return sum(values[:cut]), sum(values[cut:])


def change_permille(target, baseline):
    """The target's share minus the baseline's, in per mille; 0 when a side has no documents."""
    (k_t, n_t), (k_b, n_b) = target, baseline
    if not n_t or not n_b:
        return 0
    return stats.round_half_away((min(k_t, n_t) / n_t - min(k_b, n_b) / n_b) * 1000.0)


def judge(rows, documents, target):
    """Where each row's share is going, and how sure the counts are. `rows` hold the documents
    naming each thing per month, `documents` the documents read per month (oldest first), `target`
    the last months that are the period's own.

    A row gets two tests, the target against the baseline and the trend over every month. The tests
    of all the rows that are named at all are one family under the false-discovery rate. A row
    named in no document is `unclear` and takes no part in the family."""
    documents_baseline, documents_now = sides(documents, target)
    tested = []
    for row in rows:
        baseline, now = sides(row, target)
        tested.append((baseline, now, stats.log_odds_z(now, documents_now, baseline, documents_baseline, STEP_PRIOR),
                       stats.trend_z(row, documents)))
    family = [score for baseline, now, step, trend in tested if baseline or now for score in (step, trend)]
    survives = iter(stats.benjamini_hochberg(family, stats.FALSE_DISCOVERY_RATE))
    readings = []
    for baseline, now, step, trend in tested:
        direction = "unclear"
        if baseline or now:
            direction = stats.call_direction(
                step, trend, next(survives, False), next(survives, False),
                change_permille((now, documents_now), (baseline, documents_baseline)), min(documents_baseline, documents_now))
        low, high = stats.wilson(now, documents_now)
        readings.append({"direction": direction, "target": now, "target_permille": stats.permille(now, documents_now),
                         "low_permille": low, "high_permille": high, "step_z": step, "trend_z": trend})
    return readings


class Tally:
    """Every skill of a vocabulary over some documents (those outside the window are ignored)."""

    def __init__(self, docs, vocab, window):
        months, self.target = window
        column = {month: at for at, month in enumerate(months)}
        self.documents = [0] * len(months)
        self.table = [[0] * len(months) for _ in vocab.names]
        for doc in docs:
            at = column.get(doc.month)
            if at is None:
                continue
            self.documents[at] += 1
            for skill in vocab.skills_in(doc.text):
                self.table[skill][at] += 1
        self.readings = judge(self.table, self.documents, self.target)

    def total(self, skill):
        return sum(self.table[skill]) if skill < len(self.table) else 0

    def direction(self, skill):
        return self.readings[skill]["direction"] if skill < len(self.readings) else "unclear"

    def has_both_sides(self):
        baseline, target = sides(self.documents, self.target)
        return baseline >= stats.MIN_SIDE_DOCS and target >= stats.MIN_SIDE_DOCS

    def scope(self, vocab, profile=(), wished=()):
        """The scope's list: skills that appear at all, most mentioned first, ties by name, at most
        `MAX_SKILLS`, each with its direction, numbers and marks."""
        skills = []
        for at, name in enumerate(vocab.names):
            if not self.total(at):
                continue
            reading = self.readings[at]
            skills.append({
                "skill": name, "by_month": list(self.table[at]), "total": self.total(at), "direction": reading["direction"],
                "target": reading["target"], "target_permille": reading["target_permille"],
                "low_permille": reading["low_permille"], "high_permille": reading["high_permille"],
                "z_centi": stats.centi(reading["step_z"]), "trend_z_centi": stats.centi(reading["trend_z"]),
                "in_profile": at in profile, "wished": at in wished,
            })
        skills.sort(key=lambda skill: (-skill["total"], skill["skill"]))
        return {"documents": list(self.documents), "skills": skills[:MAX_SKILLS]}


def count_skills(docs, vocab, window):
    """Skill frequency over `docs`: the global scope alone."""
    scope = Tally(docs, vocab, window).scope(vocab)
    return {"months": list(window[0]), "target_months": window[1], "documents": scope["documents"], "skills": scope["skills"]}


# --- the gap and the wishes ----------------------------------------------------------------------

def gap_bucket(direction, in_profile, share_permille):
    """Where a skill goes in the user's gap:

    - in the profile and falling: at risk;
    - in the profile and rising, or steady with a share of at least `HIGH_SHARE_PERMILLE`: build on;
    - not in the profile, not falling, and rising or at a high share: learn next;
    - otherwise not in the gap. `unclear` is treated like steady: only the share rule applies."""
    high = share_permille >= HIGH_SHARE_PERMILLE
    if direction == "falling":
        return "at_risk" if in_profile else None
    if direction == "rising" or high:
        return "build_on" if in_profile else "learn_next"
    return None


_BUCKET_RANK = {"build_on": 0, "learn_next": 1, "at_risk": 2}


def gap_list(field, vocab, profile):
    """The gap over the field's postings, by bucket, then share, then name; at most `MAX_PER_BUCKET` each."""
    documents = sum(field.documents)
    gap = []
    for at, name in enumerate(vocab.names):
        reading = field.readings[at]
        share = stats.permille(field.total(at), documents)
        bucket = gap_bucket(reading["direction"], at in profile, share)
        if bucket:
            gap.append({"skill": name, "bucket": bucket, "direction": reading["direction"], "share_permille": share,
                        "target_permille": reading["target_permille"], "low_permille": reading["low_permille"],
                        "high_permille": reading["high_permille"]})
    gap.sort(key=lambda skill: (_BUCKET_RANK[skill["bucket"]], -skill["share_permille"], skill["skill"]))
    kept, per_bucket = [], {}
    for skill in gap:
        per_bucket[skill["bucket"]] = per_bucket.get(skill["bucket"], 0) + 1
        if per_bucket[skill["bucket"]] <= MAX_PER_BUCKET:
            kept.append(skill)
    return kept


def _strings(value):
    if isinstance(value, str):
        value = [value]
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def wish_list(prefs):
    """The wishes of the preferences in their order: wanted and nice-to-have skills, avoided
    skills, wanted domains, avoided domains; the first of two that differ only in case wins."""
    lists = (("must_skills", "skill", False), ("nice_skills", "skill", False), ("avoid_skills", "skill", True),
             ("domains", "domain", False), ("avoid_domains", "domain", True))
    seen, wishes = set(), []
    for key, kind, avoided in lists:
        for wish in _strings(prefs.get(key)):
            wish = tx.one_line(wish)
            if wish and (kind, wish.lower()) not in seen:
                seen.add((kind, wish.lower()))
                wishes.append((wish, kind, avoided))
    return wishes


def wish_signals(wishes, docs, profile_skills, window):
    """Each wish measured on the gap's scope (`docs`): a skill through a vocabulary that knows the
    wished skills too (only here: the market lists never use it), a domain as a word in the
    posting. No mention at all is "no data" (0, `unclear`)."""
    wished = [wish for wish, kind, _ in wishes if kind == "skill"]
    vocab = Vocabulary(list(profile_skills) + wished)
    tally = Tally(docs, vocab, window)
    column = {month: at for at, month in enumerate(window[0])}
    domain_rows = []
    for wish, kind, _ in wishes:
        if kind != "domain":
            continue
        by_month = [0] * len(window[0])
        for doc in docs:
            at = column.get(doc.month)
            if at is not None and tx.mentions_lowered(doc.lower, wish):
                by_month[at] += 1
        domain_rows.append(by_month)
    domains = iter(zip(judge(domain_rows, tally.documents, window[1]), domain_rows))
    signals = []
    for wish, kind, avoided in wishes:
        if kind == "skill":
            found = vocab.find(wish)
            direction, mentions = (tally.direction(found[0]), tally.total(found[0])) if found else ("unclear", 0)
        else:
            reading, row = next(domains)
            direction, mentions = reading["direction"], sum(row)
        signals.append({"wish": wish, "kind": kind, "avoided": avoided,
                        "direction": direction if mentions else "unclear", "mentions": mentions})
    return signals


def market_counts(frame, prefs, postings, window):
    """Everything the counts say about skills, from the market's `postings` (already limited to
    the window). The terms and the coverage are added by `counts_for`."""
    # The market vocabulary knows the background, never the wishes.
    vocab = Vocabulary(frame.skills)
    wishes = wish_list(prefs)
    profile = vocab.find_all(frame.skills)
    wished = vocab.find_all([wish for wish, kind, _ in wishes if kind == "skill"])

    scope = Tally(postings, vocab, window).scope(vocab, profile, wished)
    local_docs = [doc for doc in postings if frame.is_local(doc.lower)] if frame.has_place() else None
    local_tally = Tally(local_docs, vocab, window) if local_docs is not None else None
    if local_tally is not None and local_tally.has_both_sides():
        gap_scope, scope_docs = "local", local_docs
    else:
        gap_scope, scope_docs = "global", postings
    field = Tally([doc for doc in scope_docs if frame.in_field(doc.lower)], vocab, window)
    return {
        "version": MARKET_SCAN_VERSION,
        "months": list(window[0]),
        "target_months": window[1],
        "documents": scope["documents"],
        "skills": scope["skills"],
        "local": local_tally.scope(vocab, profile, wished) if local_tally is not None else None,
        "place": frame.place_label(),
        "field": list(frame.role_words),
        "gap_scope": gap_scope,
        "gap": gap_list(field, vocab, profile),
        "uncatalogued": [],
        "wishes": wish_signals(wishes, scope_docs, frame.skills, window),
        "coverage": {},
    }


# --- the items of a period -----------------------------------------------------------------------

def is_hiring_posting(text):
    """A "Who is hiring" posting as the Hacker News adapter writes it: its text starts with
    `# Hacker News, Ask HN: Who is hiring? (...)`."""
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "").lower()
    return first.startswith("# hacker news,") and "who is hiring" in first


def stratum_of(item, kinds):
    """The kind of an item: a posting by its header, any other by the adapter of its source. An
    interview or a talk is prose like a feed entry; an item whose source is gone is `other`."""
    if item["url"].startswith(HN_ITEM_PREFIX) and is_hiring_posting(item.get("text")):
        return "hiring_posting"
    kind = kinds.get(item.get("source_id"))
    return {"hackernews": "hn_story", "yc": "yc", "rss": "feed", "page": "feed", "person": "feed"}.get(kind, "other")


def period_items(items, period, kinds):
    """Every item a report of `period` is about, newest first, each with its stratum: published or
    fetched inside the period. A Hacker News item carries its real time: fetched in the period, it
    counts when it is at most a month old (this month's hiring thread); a posting backfilled from
    an older thread belongs to its own month, not to the run that fetched it."""
    end = period.end()
    floor = end - datetime.timedelta(days=CURRENT_THREAD_DAYS)
    found = []
    for item in items:
        published, fetched = tx.from_iso(item.get("published_at")), tx.from_iso(item.get("fetched_at"))
        if published is not None and item["url"].startswith(HN_ITEM_PREFIX):
            inside = period.contains(published) or (period.contains(fetched) and published >= floor)
        else:
            inside = period.contains(published) or period.contains(fetched)
        if inside:
            found.append((item, stratum_of(item, kinds)))
    found.sort(key=lambda pair: (store.moment_of(pair[0]) or period.start(), pair[0]["id"]), reverse=True)
    return found


def coverage_lines(coverage, months):
    """What the counts saw and did not see, as plain sentences: the same for the brief, the
    Markdown and the PDF, so they never disagree."""
    if months and months[0] != months[-1]:
        over = f" over {months[0]} to {months[-1]}"
    elif months:
        over = f" in {months[0]}"
    else:
        over = ""
    counted = coverage.get("postings_counted", 0)
    lines = ["No hiring postings were counted" + over + "." if counted == 0 else
             f"Counted 1 hiring posting{over}." if counted == 1 else f"Counted {counted} hiring postings{over}."]
    unreadable = coverage.get("postings_unreadable", 0)
    if unreadable == 1:
        lines.append("1 Hacker News item could not be read; it may have been a posting.")
    elif unreadable > 1:
        lines.append(f"{unreadable} Hacker News items could not be read; some may have been postings.")
    left = coverage.get("left_unread_at_source")
    if left == 1:
        lines.append("In this run the sources left 1 item unread.")
    elif left and left > 1:
        lines.append(f"In this run the sources left {left} items unread.")
    # When no posting was counted at all, the first sentence has said it.
    if counted > 0 and coverage.get("months_without_postings"):
        lines.append("No hiring postings were counted in " + ", ".join(coverage["months_without_postings"]) + ".")
    if coverage.get("not_comparable"):
        kinds = ", ".join(STRATUM_PLURAL[kind] for kind in coverage["not_comparable"])
        lines.append(f"Not compared with the period before (too few items or too uneven): {kinds}.")
    return lines


def counts_for(period, items=None, frame=None, prefs=None, today=None):
    """The counts of a report of `period` over every stored item (never a sample)."""
    from . import frame as frames

    items = store.Items().items if items is None else items
    if frame is None:
        frame, prefs = frames.load()
    prefs = prefs or {}
    window = window_for(period, today or clock.today())
    months, target = window
    sources = store.load_sources()
    kinds, people = store.source_kinds(sources), store.people(sources)

    postings, unreadable = [], 0
    for item in items:
        moment = store.moment_of(item)
        if not item["url"].startswith(HN_ITEM_PREFIX) or moment is None or moment.strftime("%Y-%m") not in months:
            continue
        if not isinstance(item.get("text"), str):
            unreadable += 1
        elif is_hiring_posting(item["text"]):
            postings.append(Doc(moment.strftime("%Y-%m"), item["text"]))
    counts = market_counts(frame, prefs, postings, window)

    # The words of every item, per kind of item: postings over the window's months, every other
    # kind over this period against the one before. The items of followed people are left out: what
    # a person says in an interview is an opinion the report quotes, never part of the numbers.
    baseline_months = len(months) - target
    corpora = terms.Corpora(baseline_months, target)
    for doc in postings:
        corpora.add("hiring_posting", "target" if months.index(doc.month) >= baseline_months else "baseline", doc.text)
    of_market = lambda pair: pair[0].get("source_id") not in people  # noqa: E731
    now_items = [pair for pair in period_items(items, period, kinds) if of_market(pair)]
    previous = period.previous()
    in_target = {item["id"] for item, _ in now_items}
    before = [pair for pair in period_items(items, previous, kinds) if of_market(pair) and pair[0]["id"] not in in_target]
    counted = len([1 for _, stratum in now_items if stratum == "hiring_posting"])
    for of_period, (item, stratum) in [(True, pair) for pair in now_items] + [(False, pair) for pair in before]:
        if stratum == "hiring_posting" or not isinstance(item.get("text"), str):
            continue
        moment = store.moment_of(item)
        side = "target" if period.contains(moment) else "baseline" if previous.contains(moment) else None
        if side:
            corpora.add(stratum, side, item["text"])
        counted += 1 if of_period else 0
    found, not_comparable = corpora.report(Vocabulary(frame.skills))
    counts["uncatalogued"] = found
    run = store.load_state().get("runs", {}).get(period.label())
    counts["coverage"] = {
        "postings_counted": len(postings),
        "postings_unreadable": unreadable,
        "items_counted": counted,
        "left_unread_at_source": run.get("left_unread") if isinstance(run, dict) else None,
        "months_without_postings": [month for month, read in zip(months, counts["documents"]) if read == 0],
        "not_comparable": not_comparable,
    }
    return counts
