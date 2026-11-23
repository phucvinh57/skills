"""Run every enabled source for a period and store what is new.

A slow or failing source never fails the run: each source reports its own status. A `page` (or a
person's own page) behind a login or a bot wall is never solved or retried here: its status is
`needs_human` and the user decides. Errors never carry a feed's address (it may hold a token)."""
from .. import http as net
from . import config, hackernews, page, person, rss, store, yc
from . import period as clock
from . import text as tx


def challenge_of(kind, error):
    """Tells a login or bot wall from an ordinary failure, by status code. Only a page the user
    pointed at can be one: API adapters never are."""
    if kind not in ("page", "person") or not isinstance(error, net.HttpError):
        return None
    if error.status == 401:
        return "login"
    if error.status in (403, 429, 503):
        return "blocked"
    return None


def _run_source(source, ctx, settled):
    """`(items, cut, extra)` of one source; `extra` holds what Hacker News adds."""
    kind = source["kind"]
    if kind == "hackernews":
        items, cut, now_settled, threads_left, failed_parts = hackernews.collect(ctx, settled)
        return items, cut, {"settled": now_settled, "threads_left": threads_left, "failed_parts": failed_parts}
    if kind == "yc":
        return yc.collect(ctx) + ({},)
    if kind == "rss":
        return rss.collect(ctx, source.get("url") or "") + ({},)
    if kind == "page":
        return page.collect(ctx, source.get("url") or "") + ({},)
    items, cut, failed_parts = person.collect(ctx, source.get("name") or "", source.get("url") or "")
    return items, cut, {"failed_parts": failed_parts}


def run(period):
    """Collect `period` from every enabled source. Returns the per-source outcomes and totals."""
    sources = store.load_sources()
    people = store.people(sources)
    items = store.Items()
    saved = store.load_state()
    settled = set(key for key in saved.get("hn_settled", []) if isinstance(key, str))
    outcomes, new_settled, total_new, total_cut = [], [], 0, 0
    for source in sources:
        outcome = {"id": source["id"], "kind": source["kind"], "name": source.get("name", ""), "items_read": 0, "new": 0,
                   "cut": 0}
        outcomes.append(outcome)
        if not source.get("enabled", True):
            outcome["status"] = "skipped"
            outcome["reason"] = "the source is turned off"
            continue
        # A person's source reads again what another source stored with a title alone.
        known = items.person_urls(people) if source["kind"] == "person" else items.urls()
        ctx = hackernews.Budget(period, known)
        try:
            found, cut, extra = _run_source(source, ctx, settled)
        except (net.HttpError, page.Unreadable, rss.NotFeed) as error:
            challenge = challenge_of(source["kind"], error)
            outcome["status"] = "needs_human" if challenge else "failed"
            outcome["error"] = tx.truncate(tx.one_line(str(error)), 200)
            if challenge:
                outcome["challenge"] = challenge
            source["last_status"] = outcome["status"]
            continue
        except Exception as error:  # one broken source must not stop the others
            outcome["status"] = "failed"
            outcome["error"] = f"unexpected {type(error).__name__}"
            source["last_status"] = "failed"
            continue
        for item in found:
            item["text"] = tx.cap(item["text"], config.MAX_TEXT_CHARS)
            outcome["new"] += 1 if items.add(source, item, people) else 0
        outcome.update(status="ok", items_read=len(found), cut=cut)
        if extra.get("threads_left"):
            outcome["hiring_threads_left"] = extra["threads_left"]
        if extra.get("failed_parts"):
            outcome["note"] = "one part of this source could not be read; the other parts are kept"
        new_settled.extend(extra.get("settled", []))
        total_new += outcome["new"]
        total_cut += cut
        source["last_status"] = "ok"
        source["last_collected_at"] = tx.iso(clock.now())
        items.save()  # after every source: what one source read is kept whatever the next one does
    store.save_sources(sources)
    saved = store.load_state()
    if new_settled:
        saved["hn_settled"] = hackernews.merge_settled(saved.get("hn_settled", []), new_settled)
    runs = saved.setdefault("runs", {})
    runs[period.label()] = {"at": tx.iso(clock.now()), "left_unread": total_cut}
    store.save_state(saved)
    return {"period": period.label(), "sources": outcomes, "new": total_new, "left_unread": total_cut,
            "items_stored": len(items.items)}
