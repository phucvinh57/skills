"""Where the adapters send requests and how hard they may push. Tests point the endpoints at a
local fixture server; no command changes them."""

# Base URLs of the APIs the adapters call.
ENDPOINTS = {
    # HN Algolia API root (`<root>/search`, `<root>/search_by_date`, `<root>/items/<id>`).
    "hackernews": "https://hn.algolia.com/api/v1",
    # The YC company directory mirror (`<root>/meta.json`, `<root>/batches/<slug>.json`): YC has no
    # JSON API of its own; this is the community `yc-oss` mirror (static files, refreshed daily).
    "yc": "https://yc-oss.github.io/api",
}

# --- collection ---
MAX_TEXT_CHARS = 100_000      # longest text kept per item; the rest is cut
MAX_ITEMS = 100               # new items a feed, the YC directory or a person adds per run (the rest is counted as cut)
HN_FRONT_PAGE = 30            # front-page stories read per run
HN_PERIOD_STORIES = 100       # stories of the period per request
HN_STORY_PAGES = 10           # pages of the period's stories per run at most
HN_MIN_POINTS = 100
HIRING_THREADS = 6            # "Who is hiring" threads per run for a week, month or quarter
YC_BATCHES = 2                # newest YC batches read
ARTICLE_FETCHES = 20          # feed entries per run whose page is fetched (the feed has only a teaser)
PERSON_STORIES = 30           # stories naming a followed person asked for per run
PERSON_MIN_POINTS = 10
PERSON_FETCHES = 12           # stories per person and run whose linked page is fetched
SOURCE_TIMEOUT = 180.0        # seconds one source may take; it stops starting work at two thirds of it
BACKFILL_SHARE = 2.0 / 3.0
HN_SETTLED_KEEP = 24          # settled hiring threads remembered (two years of monthly threads)

# --- the digest ---
DIGEST_MAX_ITEMS = 240        # items a weekly report reads; a longer period reads more (`for_period`)
BATCH_ITEMS = 6               # items per evidence batch
ITEM_CHARS = 4_000            # characters of one item's text the agent sees
PERSON_ITEM_FACTOR = 3        # a followed person's item is shown this many times longer
EVIDENCE_CHARS = 40_000       # characters of evidence in the brief
MAX_EVIDENCE_CHARS = 160_000  # the most a longer period's evidence grows to


def hiring_threads_for(period):
    """A year reads four times as many threads: its twelve months and twelve before them."""
    return HIRING_THREADS * 4 if period.unit == "year" else HIRING_THREADS


def period_factor(period):
    return {"week": 1, "month": 2, "quarter": 4, "year": 8}[period.unit]


def digest_limits(period):
    """`(max_items, evidence_chars)` for a report of `period`."""
    factor = period_factor(period)
    return DIGEST_MAX_ITEMS * factor, min(EVIDENCE_CHARS * factor, max(MAX_EVIDENCE_CHARS, EVIDENCE_CHARS))


# The sections of a report, in display order, as the points and the stored JSON spell them.
SECTIONS = ("rising_skills", "falling_skills", "domains", "funding", "hiring", "outlook", "for_you", "wishes_check")
SECTION_TITLES = {
    "rising_skills": "Rising skills",
    "falling_skills": "Falling skills",
    "domains": "Active domains",
    "funding": "Funding and launches",
    "hiring": "Hiring signals",
    "outlook": "Outlook: what the people you follow say",
    "for_you": "Your gap: build on, learn next, at risk",
    "wishes_check": "Your wishes vs the market",
}
