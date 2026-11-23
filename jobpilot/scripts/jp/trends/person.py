"""A person the user follows (a founder, a CEO, a researcher): what they said in interviews, talks
and conversations, as items the report can quote and link.

Two places are read, both read-only:

1. Stories that name them: the Hacker News search for stories whose title carries the person's
   name, newest first, from a month before the period to its end. The story's link is the
   interview, the talk or the article; its page is fetched so the report reads the words, not only
   the headline. A page that cannot be read (a video, a paywall) leaves an item with the title
   alone. A run fetches at most `PERSON_FETCHES` pages; the stories it did not get to are left for
   the next run and counted as cut.
2. An address of their own, when the user gave one: a feed or a page. Every entry counts as theirs.

A story is about the person, which is not the same as the person speaking: every statement the
report attributes to them carries a quote found in the item (`cite`)."""
import datetime

from .. import http as net
from . import config, hackernews, page, rss, yc
from . import text as tx

# Stories this long before the period start are still read.
BACKLOG_DAYS = 30


def own_address(ctx, url):
    """The person's own address: a feed, else a page. A feed that cannot be fetched is an error
    (the page would fail the same way)."""
    normal = tx.normalize_url(url)
    if normal is None:
        raise page.Unreadable("their address is not an http(s) address")
    if normal in ctx.known:
        return [], 0
    try:
        return rss.collect(ctx, url)
    except rss.NotFeed:
        return page.collect(ctx, url)


def stories(ctx, name):
    """The stories naming the person, each with the text of the page it links when that can be
    read, and how many stories were left unread."""
    since = ctx.period.start() - datetime.timedelta(days=BACKLOG_DAYS)
    found, cut = hackernews.stories_naming(ctx, name, since)
    items, seen, fetched = [], set(), 0
    for item, linked in found:
        if item["url"] in ctx.known or item["url"] in seen:
            continue
        seen.add(item["url"])
        if linked and rss._fetchable(item["url"]):
            # Not stored with its title alone: it would be known, and its page never read.
            if fetched >= config.PERSON_FETCHES or ctx.out_of_time():
                cut += 1
                continue
            fetched += 1
            try:
                item = page.make_item(item["url"], item["title"], item["published_at"], page.fetch_text(item["url"])[1])
            except (net.HttpError, page.Unreadable):
                pass  # the headline and the link are still worth keeping
        items.append(item)
    return items, cut


def collect(ctx, name, url):
    """The new items about `name`, how many more a cap cut, and how many places failed. A part
    that fails is left out; the adapter fails only when nothing was read."""
    items, cut, failures = [], 0, []
    if url:
        try:
            found, unread = own_address(ctx, url)
            items.extend(found)
            cut += unread
        except (net.HttpError, page.Unreadable) as err:
            failures.append(err)
    try:
        found, unread = stories(ctx, name)
        items.extend(found)
        cut += unread
    except net.HttpError as err:
        failures.append(err)
    items, more = yc.take_new(ctx.known, items, config.MAX_ITEMS)
    if failures and not items:
        raise failures[0]
    return items, cut + more, len(failures)
