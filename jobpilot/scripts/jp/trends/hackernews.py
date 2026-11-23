"""Hacker News through the Algolia API: the front page, the period's most upvoted stories, and the
newest "Ask HN: Who is hiring?" threads before the period's end (one item per posting; several
monthly threads, so the skill window is full from the first run; an older thread read in full is
not downloaded again until its comment count changes).

Nothing is cut silently: what the page cap or the time budget leaves unread of the stories is
counted (`cut`), the hiring threads not started too (`threads_left`).

A story's item is the story itself (its link, or the HN thread for text posts): the title and the
numbers are the text; the linked article is not fetched."""
import time
import urllib.parse

from .. import http as net
from . import config
from . import text as tx

# Permalink of an HN item (story or comment).
ITEM_URL = "https://news.ycombinator.com/item?id="
# A hiring comment shorter than this is a reply or a stray remark, not a posting.
MIN_POSTING_CHARS = 40


def settled_key(thread, comments):
    """How a settled thread is remembered: its id and its comment count when it was read (`903@412`)."""
    return f"{thread}@{comments}"


def settled_thread(key):
    """The thread id of a settled key; None for anything else."""
    thread, _, comments = str(key).partition("@")
    return int(thread) if thread.isdigit() and comments.isdigit() else None


def _key_of(thread):
    count = thread.get("num_comments")
    return settled_key(thread.get("objectID"), count) if isinstance(count, int) and count >= 0 else None


def api_url(path, query):
    return config.ENDPOINTS["hackernews"].rstrip("/") + "/" + path + "?" + urllib.parse.urlencode(query)


def _search(url):
    answer = net.client().get_json(url)
    if not isinstance(answer, dict):
        raise net.HttpError("Hacker News answered with something that is not a search result")
    hits = [hit for hit in answer.get("hits") or [] if isinstance(hit, dict)]
    count = lambda key: answer.get(key) if isinstance(answer.get(key), int) else 0  # noqa: E731
    return hits, count("nbPages"), count("nbHits")


def _number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def story_item(hit):
    """A story as an item (None without a title): its link, else the thread."""
    title = hit.get("title").strip() if isinstance(hit.get("title"), str) else ""
    if not title:
        return None
    discussion = f"{ITEM_URL}{hit.get('objectID')}"
    url = (tx.normalize_url(hit["url"]) if isinstance(hit.get("url"), str) else None) or tx.normalize_url(discussion)
    if url is None:
        return None
    text = (f"# {title}\n\n{_number(hit.get('points'))} points and {_number(hit.get('num_comments'))} comments on "
            f"Hacker News. Discussion: {discussion}")
    body = tx.html_to_text(hit["story_text"]) if isinstance(hit.get("story_text"), str) else ""
    if body:
        text += "\n\n" + body
    return {"url": url, "title": title, "published_at": tx.from_timestamp(hit.get("created_at_i")), "text": text}


def front_page():
    hits, _, _ = _search(api_url("search", [("tags", "front_page"), ("hitsPerPage", config.HN_FRONT_PAGE)]))
    return [item for item in map(story_item, hits) if item]


def period_stories(ctx):
    """The period's stories with enough points, newest first, page by page until the last page, at
    most `HN_STORY_PAGES` pages. Returns the stories and how many hits were left unread (the page
    cap, the time budget or a failing later page cut them). A failing first page is an error."""
    filters = (f"created_at_i>={int(ctx.period.start().timestamp())},created_at_i<{int(ctx.period.end().timestamp())},"
               f"points>={config.HN_MIN_POINTS}")
    items, read, total = [], 0, 0
    for number in range(max(config.HN_STORY_PAGES, 1)):
        if number > 0 and ctx.out_of_time():
            break
        url = api_url("search_by_date", [("tags", "story"), ("numericFilters", filters),
                                         ("hitsPerPage", config.HN_PERIOD_STORIES), ("page", number)])
        try:
            hits, pages, total = _search(url)
        except net.HttpError:
            if number == 0:
                raise
            break
        read += len(hits)
        items.extend(item for item in map(story_item, hits) if item)
        if number + 1 >= pages or not hits:
            # The API serves only the first hits of a large result: the last page it offers is not
            # always the end of the result.
            return items, max(total - read, 0)
    return items, max(total - read, 1)


def stories_naming(ctx, name, since):
    """The stories from `since` to the period's end whose title names `name` (the whole name, as a
    phrase, whatever its case), newest first, as `(item, linked)` pairs; and how many more hits the
    search has past this page. The title is checked again here, so a loose search adds nothing."""
    name = tx.one_line(name.replace('"', " "))
    if not name:
        raise net.HttpError("a person to follow needs a name")
    filters = (f"created_at_i>={int(since.timestamp())},created_at_i<{int(ctx.period.end().timestamp())},"
               f"points>={config.PERSON_MIN_POINTS}")
    url = api_url("search_by_date", [("query", f'"{name}"'), ("tags", "story"), ("restrictSearchableAttributes", "title"),
                                     ("numericFilters", filters), ("hitsPerPage", config.PERSON_STORIES)])
    hits, _, total = _search(url)
    stories = []
    for hit in hits:
        linked = isinstance(hit.get("url"), str) and tx.normalize_url(hit["url"]) is not None
        item = story_item(hit)
        if item and tx.mentions(item["title"], name):
            stories.append((item, linked))
    return stories, max(total - len(hits), 0)


def hiring_threads(hits, end, limit):
    """The "Who is hiring" threads among `hits` that started before `end`, newest first, at most `limit`."""
    threads = [hit for hit in hits
               if isinstance(hit.get("title"), str) and "who is hiring" in hit["title"].lower()
               and isinstance(hit.get("created_at_i"), int) and hit["created_at_i"] < end]
    threads.sort(key=lambda hit: hit["created_at_i"], reverse=True)
    seen, unique = set(), []
    for thread in threads:
        if thread.get("objectID") not in seen:
            seen.add(thread.get("objectID"))
            unique.append(thread)
    return unique[:limit]


def thread_posts(ctx, thread):
    """The new postings of one thread, all of them. Each keeps the thread's title in its text
    header (`# Hacker News, <title>`): the counts recognise hiring postings by it."""
    title = thread.get("title") or ""
    full = net.client().get_json(config.ENDPOINTS["hackernews"].rstrip("/") + f"/items/{thread.get('objectID')}")
    children = full.get("children") if isinstance(full, dict) else None
    postings, seen = [], set()
    for comment in children if isinstance(children, list) else []:
        if not isinstance(comment, dict) or not isinstance(comment.get("text"), str) or not isinstance(comment.get("id"), int):
            continue
        text = tx.html_to_text(comment["text"])
        headline = tx.first_line(text, 120)
        url = f"{ITEM_URL}{comment['id']}"
        if len(text) < MIN_POSTING_CHARS or not headline or url in ctx.known or url in seen:
            continue
        seen.add(url)
        postings.append({"url": url, "title": headline, "published_at": tx.from_timestamp(comment.get("created_at_i")),
                         "text": f"# Hacker News, {title}\n\n{text}"})
    return postings


def hiring_posts(ctx, settled):
    """The newest "Who is hiring" threads that started before the period ended, one item per
    posting: `(items, settled keys of the older threads read in full, older threads left unread)`.
    An older thread whose settled key is known is not downloaded; the newest is always read. A
    thread that cannot be read is left out; this part fails only when every thread did."""
    end = int(ctx.period.end().timestamp())
    wanted = config.hiring_threads_for(ctx.period)
    # The bot posts three threads a month ("Who is hiring", "Who wants to be hired", "Freelancer").
    hits = min(max(wanted * 3 + 3, 12), 100)
    listed, _, _ = _search(api_url("search_by_date", [("tags", "story,author_whoishiring"),
                                                      ("numericFilters", f"created_at_i<{end}"), ("hitsPerPage", hits)]))
    threads = hiring_threads(listed, end, wanted)
    is_settled = lambda thread: _key_of(thread) is not None and _key_of(thread) in settled  # noqa: E731
    items, now_settled, read, threads_left, first_error = [], [], 0, 0, None
    for number, thread in enumerate(threads):
        newest = number == 0
        if not newest and is_settled(thread):
            continue
        if not newest and ctx.out_of_time():
            threads_left = len([left for left in threads[number:] if not is_settled(left)])
            break
        try:
            items.extend(thread_posts(ctx, thread))
        except net.HttpError as err:
            first_error = first_error or err
            continue
        read += 1
        if not newest and _key_of(thread):
            now_settled.append(_key_of(thread))
    if first_error is not None and read == 0:
        raise first_error
    return items, now_settled, threads_left


def collect(ctx, settled):
    """Stories and hiring postings: `(items, cut, settled, threads_left, failed parts)`. A part that fails is
    left out; the adapter fails only when every part did. Every new story is kept: the page cap
    bounds them, not `MAX_ITEMS`."""
    stories, failures, cut = [], [], 0
    try:
        stories.extend(front_page())
    except net.HttpError as err:
        failures.append(err)
    try:
        found, unread = period_stories(ctx)
        stories.extend(found)
        cut += unread
    except net.HttpError as err:
        failures.append(err)
    seen, items = set(), []
    for item in stories:
        if item["url"] not in ctx.known and item["url"] not in seen:
            seen.add(item["url"])
            items.append(item)
    now_settled, threads_left = [], 0
    try:
        postings, now_settled, threads_left = hiring_posts(ctx, settled)
        items.extend(postings)
    except net.HttpError as err:
        failures.append(err)
    if failures and not items:
        raise failures[0]
    return items, cut, now_settled, threads_left, len(failures)


def merge_settled(saved, new):
    """`new` merged into the saved keys: one key per thread (a new key replaces the saved one of
    its thread), the newest threads first, at most `HN_SETTLED_KEEP`."""
    by_thread = {}
    for key in list(new) + list(saved):
        thread = settled_thread(key)
        if thread is not None and thread not in by_thread:
            by_thread[thread] = key
    return [by_thread[thread] for thread in sorted(by_thread, reverse=True)[:config.HN_SETTLED_KEEP]]


class Budget:
    """What an adapter needs besides its own source: what is known, the period, and the clock."""

    def __init__(self, period, known):
        self.period, self.known = period, known
        self.started = time.monotonic()

    def out_of_time(self):
        """Two thirds of the source's time are spent: stop starting pages and threads, keep what is in hand."""
        return time.monotonic() - self.started >= config.SOURCE_TIMEOUT * config.BACKFILL_SHARE
