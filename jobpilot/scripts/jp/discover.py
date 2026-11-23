"""Job sources and the discovery run.

`sources.json` is a list of `{id, kind, name, url, purpose, enabled, last_run_at, last_status}`;
the rows with purpose `jobs` are the job sources (other purposes belong to other commands and are
left as they are).

The run, per enabled source:

    search (a page) -> dedup (inside the run, against every lead and job) -> posting text
      -> hard filters -> dismissed with the reason | new, waiting for a score

- At most `PAGES_PER_RUN` pages that bring something new, `PAGE_SIZE` postings each; a page whose
  postings are all known costs only a request, so `SCAN_FACTOR` times as many may be looked at.
- A source that fails (network, a page that needs a browser, a login or a captcha) ends only its own
  part of the run: its `last_status` says why. Nothing is retried, solved or worked around.
- Discovery never rates, never promotes and never writes a CV.
"""
from . import dedup, http as net, leads, prefs as prefs_model, score as scoring, sources, state
from .cli import Fail

FILE = "sources.json"
PAGES_PER_RUN = 3
PAGE_SIZE = 25
SCAN_FACTOR = 10
MIN_POSTING_CHARS = 40  # less text than this is not a posting (a bot page, an empty app shell)
SNIPPET_CHARS = 280
CHECK_PAGE_SIZE = 500  # most postings asked of a board when it is added
MAX_UNANSWERED = 3  # postings in a row that got no answer before the source stops
NOT_READ = ("hn_hiring", "yc")  # kinds added without a read


# --- sources.json --------------------------------------------------------------------------------

def _all_sources():
    return state.read_json(FILE, [])


def _is_jobs(row):
    return row.get("purpose", "jobs") == "jobs"


def job_sources():
    """Every job source, oldest first."""
    return [row for row in _all_sources() if _is_jobs(row)]


def _find(rows, source_id):
    for row in rows:
        if row.get("id") == source_id:
            if not _is_jobs(row):
                raise Fail(f"source {source_id} is not a job source")
            return row
    raise Fail(f"no source {source_id}", hint="`jobpilot.py source list` shows the job sources")


def kind_label(kind):
    return getattr(sources, "LABELS", {}).get(kind, kind)


def _status_of(err):
    """The HTTP status an error carries, however the adapter passed it on."""
    status = getattr(err, "status", None)
    if status is None and isinstance(err, Fail):
        status = err.extra.get("status")
    return status


def _needs_browser(err):
    kind = getattr(sources, "NeedsBrowser", None)
    return (kind is not None and isinstance(err, kind)) or (isinstance(err, Fail) and bool(err.extra.get("needs_browser")))


def _message(err):
    return scoring.one_line(getattr(err, "message", None) or str(err) or type(err).__name__, 160)


BROWSER_HINT = ("say so to the user; never work around a login, a captcha or a block. The user may open the page "
                "themselves and paste the posting (references/discover.md)")


def check(kind, url, http=None):
    """Check a job source the way a run will read it, without storing anything: the address must
    pass the adapter's own check, and the source is read once. `{kind, url, name, jobs, titles}`.

    - a company board: its first listing page, no role filter; refused when the board does not exist
      (404 / 410); a board with no open job is accepted;
    - `hn_hiring`, `yc`: not read;
    - `career_page`: refused when no posting is found; the message names the board the page points
      to when there is one."""
    checked = sources.check_source((kind or "").strip(), url or "")
    source = {"id": 0, "kind": checked["kind"], "url": checked["url"], "name": checked["name"]}
    titles = []
    if source["kind"] not in NOT_READ:
        label = kind_label(source["kind"])
        is_page = source["kind"] == "career_page"
        adapter = sources.adapter_for(source, http)
        found, failure = [], None
        try:
            found = adapter.search(prefs_model.default(), None, CHECK_PAGE_SIZE)["candidates"]
        except (Fail, net.HttpError) as err:
            failure = err
        if failure is not None and not is_page:
            if _status_of(failure) in (404, 410):
                raise Fail(f"no such board on {label}: check the address")
            if _needs_browser(failure):
                raise Fail(f"the {label} board needs a browser: {_message(failure)}", hint=BROWSER_HINT, needs_browser=True)
            raise Fail(f"checking the {label} board: {_message(failure)}", hint="a network failure: try again later")
        if is_page and not found:
            # A page that embeds a board names it even when the page itself could not be read.
            hints = adapter.board_hints()
            if hints:
                board = hints[0]
                name = board.get("label") or kind_label(board.get("kind", ""))
                raise Fail(
                    f"This page lists its jobs on {name}: add the {name} board ({board.get('url', '')})",
                    hint=f"jobpilot.py source add --kind {board.get('kind', '')} --url {board.get('url', '')}",
                    board={"kind": board.get("kind", ""), "url": board.get("url", ""), "label": name},
                )
            if failure is not None and _needs_browser(failure):
                raise Fail(f"the page needs a browser, a login or shows a captcha: {_message(failure)}",
                           hint=BROWSER_HINT, needs_browser=True)
            if failure is not None:
                raise Fail(f"reading the job page: {_message(failure)}")
            raise Fail("no job postings found on this page", hint="give the address of the page that lists the jobs")
        titles = [candidate.get("title", "") for candidate in found]
    return {"kind": source["kind"], "url": source["url"], "name": source["name"], "jobs": len(titles), "titles": titles}


def add_source(kind, url="", name="", http=None):
    """`check`, then store the source. Adding the same source again returns the stored one.
    Returns `(source, checked, added)`."""
    checked = check(kind, url, http)
    rows = _all_sources()
    for row in rows:
        if _is_jobs(row) and row.get("kind") == checked["kind"] and row.get("url") == checked["url"]:
            return row, checked, False
    source = {
        "id": max([row.get("id", 0) for row in rows] + [0]) + 1,
        "kind": checked["kind"],
        "name": (name or "").strip() or checked["name"],
        "url": checked["url"],
        "purpose": "jobs",
        "enabled": True,
        "last_run_at": None,
        "last_status": None,
    }
    rows.append(source)
    state.write_json(FILE, rows)
    return source, checked, True


def remove_source(source_id):
    """Remove a job source; the leads it found stay."""
    rows = _all_sources()
    source = _find(rows, source_id)
    state.write_json(FILE, [row for row in rows if row is not source])
    return source


def set_enabled(source_id, enabled):
    rows = _all_sources()
    source = _find(rows, source_id)
    source["enabled"] = bool(enabled)
    state.write_json(FILE, rows)
    return source


def _record_run(source_id, status):
    rows = _all_sources()
    for row in rows:
        if row.get("id") == source_id:
            row["last_run_at"] = state.now()
            row["last_status"] = status
    state.write_json(FILE, rows)


# --- the run -------------------------------------------------------------------------------------

def _skip_kind(err):
    """Why a posting could not be read, as a label (the error's text is not kept)."""
    if _needs_browser(err):
        return "needs_browser"
    status = _status_of(err)
    if status in (404, 410):
        return "gone"
    if status in (401, 403):
        return "forbidden"
    return "unreadable"


def _no_answer(err):
    """The request itself failed (no HTTP status): after `MAX_UNANSWERED` in a row the source stops,
    and a later run goes on. The postings stay unknown, so they are tried again."""
    return _status_of(err) is None and not _needs_browser(err)


def _posting(adapter, candidate, http):
    text = adapter.posting(candidate)
    if text is None:
        text = sources.fetch_posting(candidate, http)
    return text or ""


def _run_source(source, prefs, seen, limit, http):
    """One source of a run. `seen` holds the addresses and keys this run met already (also the
    postings it could not read). Returns the counts; raises what stops the source."""
    counts = {"found": 0, "new": 0, "filtered": 0, "known": 0, "skipped": {}, "ids": []}
    adapter = sources.adapter_for(source, http)
    cursor, pages, scanned, taken, unanswered = None, 0, 0, 0, 0
    while True:
        page = adapter.search(prefs, cursor, PAGE_SIZE)
        scanned += 1
        fresh = []
        for found in (page.get("candidates") or [])[:PAGE_SIZE]:
            counts["found"] += 1
            url = dedup.canonical_url(found.get("url", ""))
            if not url:
                continue
            company = (found.get("company") or "").strip() or (source.get("name") or "").strip()
            title = (found.get("title") or "").strip()
            key = dedup.dedup_key(company, title)
            if url in seen or (key and key in seen) or dedup.find_known(url, company, title):
                counts["known"] += 1
                continue
            seen.add(url)
            if key:
                seen.add(key)
            fresh.append({
                "url": url, "title": title, "company": company, "location": (found.get("location") or "").strip(),
                "snippet": (found.get("snippet") or "")[:SNIPPET_CHARS],
            })
        pages += 1 if fresh else 0
        for candidate in fresh:
            if taken >= limit:
                break
            taken += 1
            try:
                text = _posting(adapter, candidate, http).strip()
            except (Fail, net.HttpError) as err:
                # No answer at all, several times in a row: the network is down, not the postings.
                unanswered = unanswered + 1 if _no_answer(err) else 0
                if unanswered >= MAX_UNANSWERED:
                    raise
                kind = _skip_kind(err)
                counts["skipped"][kind] = counts["skipped"].get(kind, 0) + 1
                continue
            unanswered = 0
            if len(text) < MIN_POSTING_CHARS:
                counts["skipped"]["too_short"] = counts["skipped"].get("too_short", 0) + 1
                continue
            text = text[:leads.MAX_POSTING_CHARS]
            reason = scoring.hard_filter(prefs, candidate, text)
            rows = leads.load()
            lead = leads.add(rows, source["id"], candidate, candidate["company"], text, dismiss_reason=reason)
            leads.save(rows)
            counts["filtered" if reason else "new"] += 1
            counts["ids"].append(lead["id"])
        cursor = page.get("next")
        if cursor is None or taken >= limit or pages >= PAGES_PER_RUN or scanned >= PAGES_PER_RUN * SCAN_FACTOR:
            break
    counts["status"] = "ok"
    if counts["found"] == 0:
        # A page that points to a job board (a careers page embedding Greenhouse) says so.
        hints = adapter.board_hints()
        if hints:
            label = hints[0].get("label") or kind_label(hints[0].get("kind", ""))
            counts["status"] = f"No postings on the page. It links to a {label} board: add it as a {label} source"
    return counts


def run(source_id=None, limit=None, http=None):
    """Search the enabled job sources (or the one named) and store what is new. Needs preferences
    and a profile: the leads found are rated against them next."""
    prefs = prefs_model.require()
    state.load_profile()
    limit = limit if limit and limit > 0 else PAGES_PER_RUN * PAGE_SIZE
    chosen = [_find(_all_sources(), source_id)] if source_id is not None else [s for s in job_sources() if s.get("enabled", True)]
    if not chosen:
        raise Fail("there is no job source to search", hint="add one first: `jobpilot.py source add --kind ... --url ...`")
    seen, results = set(), []
    for source in chosen:
        result = {"id": source["id"], "name": source.get("name", ""), "kind": source.get("kind", ""),
                  "found": 0, "new": 0, "filtered": 0, "known": 0}
        try:
            counts = _run_source(source, prefs, seen, limit, http)
            ids = counts.pop("ids")
            skipped = counts.pop("skipped")
            result.update(counts)
            if skipped:
                result["skipped"] = skipped
            result["lead_ids"] = ids
        except Exception as err:  # one source failing must not stop the others
            if _needs_browser(err):
                result["status"] = f"needs a browser: {_message(err)}"
                result["needs_browser"] = True
            else:
                result["status"] = f"failed: {_message(err)}"
            # What it stored before it stopped is in the leads.
            mine = [lead for lead in leads.load() if lead.get("source_id") == source["id"] and lead["url"] in seen]
            result["new"] = len([lead for lead in mine if lead.get("state") == "new"])
            result["filtered"] = len([lead for lead in mine if lead.get("state") == "dismissed"])
        _record_run(source["id"], result["status"])
        results.append(result)
    out = {"sources": results, "to_score": leads.waiting_for_score()}
    if any(result.get("needs_browser") or "needs_browser" in result.get("skipped", {}) for result in results):
        out["hint"] = BROWSER_HINT
    return out
