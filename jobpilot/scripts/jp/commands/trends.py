"""`trends`: the market trends report. Collect items, count in code, the agent writes points, the
code enforces that every point cites a stored item, render to PDF (references/trends.md)."""
from .. import http as net
from .. import state
from ..cli import Fail, command, group, read_json_arg
from ..trends import brief, cite, claims, collect, count, page, period, report, rss, stats, store
from ..trends import text as tx

_WALL = "the page is behind a login or blocks automated reads: this is for the user to decide, never work around it"


def _check_address(kind, url):
    """Read a new source's address once: a feed must be a feed, a page must have readable text."""
    try:
        if kind == "rss":
            return {"kind": "feed", "entries": len(rss.read(url)[1])}
        if kind == "person":
            try:
                return {"kind": "feed", "entries": len(rss.read(url)[1])}
            except rss.NotFeed:
                pass
        return {"kind": "page", "characters": len(page.fetch_text(url)[1])}
    except rss.NotFeed as err:
        raise Fail(str(err), hint="give the address of the feed itself (RSS or Atom), or add it with --kind page")
    except page.Unreadable as err:
        raise Fail(str(err), hint="a page that needs a browser cannot be a source; look for its feed instead")
    except net.HttpError as err:
        wall = collect.challenge_of("page", err)
        raise Fail(f"the address could not be read: {err}", hint=_WALL if wall else "check the address and try again")


def _source_add(args):
    kind, sources = args.kind, store.load_sources()
    needs_url, needs_name = kind in ("rss", "page"), kind == "person"
    url = ""
    if needs_url or (needs_name and (args.url or "").strip()):
        url = tx.normalize_url(args.url or "")
        if url is None:
            raise Fail("the address of their interviews must be an http(s) address (or leave it out)" if needs_name
                       else f"{store.LABELS[kind]} needs an http(s) address", hint="pass --url https://...")
    if needs_name:
        name = tx.one_line(args.name or "")
        if not name:
            raise Fail("a person to follow needs a name", hint='pass --name "First Last"')
        if len(name) > store.MAX_PERSON_NAME_CHARS:
            raise Fail(f"that name is too long (at most {store.MAX_PERSON_NAME_CHARS} characters)")
        if any(source["kind"] == kind and source.get("name", "").lower() == name.lower() for source in sources):
            raise Fail(f"you already follow {name}")
    else:
        if any(source["kind"] == kind and source.get("url", "") == url for source in sources):
            raise Fail(f"{store.LABELS[kind] if not url else 'that address'} is already a trend source")
        name = (args.name or "").strip() or tx.host_of(url) or store.LABELS[kind]
    checked = _check_address(kind, url) if url else None
    source = {"id": max([source["id"] for source in sources] + [0]) + 1, "kind": kind, "name": name, "url": url, "enabled": True}
    store.save_sources(sources + [source])
    return {"added": source, "checked": checked}


def _source_list(args):
    items = store.Items().items
    counts = {}
    for item in items:
        counts[item.get("source_id")] = counts.get(item.get("source_id"), 0) + 1
    return {"sources": [dict(source, label=store.LABELS[source["kind"]], items=counts.get(source["id"], 0))
                        for source in store.load_sources()]}


def _find_source(sources, source_id):
    found = [source for source in sources if source["id"] == source_id]
    if not found:
        raise Fail(f"trend source {source_id} not found", hint="`trends source-list` shows the ids")
    return found[0]


def _source_remove(args):
    sources = store.load_sources()
    gone = _find_source(sources, args.id)
    store.save_sources([source for source in sources if source["id"] != args.id])
    return {"removed": gone, "note": "its items stay stored (they lose the link to the source)"}


def _source_enable(args):
    sources = store.load_sources()
    source = _find_source(sources, args.id)
    source["enabled"] = not args.off
    store.save_sources(sources)
    return {"source": source}


def _collect(args):
    chosen = period.resolve(args.period)
    result = collect.run(chosen)
    troubled = [source for source in result["sources"] if source["status"] in ("failed", "needs_human")]
    if troubled:
        result["tell_the_user"] = [
            f"{source['name'] or source['kind']} could not be read: "
            + (_WALL if source["status"] == "needs_human" else source.get("error", "unknown error"))
            for source in troubled
        ]
    result["next"] = f"trends counts --period {chosen.label()}, then trends evidence --period {chosen.label()}"
    return result


def _with_shares(scope):
    for skill in scope["skills"]:
        skill["share_by_month"] = [brief.percent(n, d) for n, d in zip(skill["by_month"], scope["documents"])]
        level = stats.level_text(1, skill["target_permille"], skill["low_permille"], skill["high_permille"])
        skill["this_period"] = f"{level[0]} ({level[1]})" if level else None
    return scope


def _counts(args):
    chosen = period.resolve(args.period)
    counts = count.counts_for(chosen)
    _with_shares(counts)
    if counts.get("local"):
        _with_shares(counts["local"])
    return {
        "period": chosen.label(),
        "note": "These numbers are final: never compute, round or extrapolate from them.",
        "completeness": count.coverage_lines(counts["coverage"], counts["months"]),
        "global": {"months": counts["months"], "target_months": counts["target_months"], "documents": counts["documents"],
                   "skills": counts["skills"]},
        "local": dict(counts["local"], place=counts["place"]) if counts.get("local") else None,
        "field": counts["field"],
        "gap_scope": counts["gap_scope"],
        "gap": counts["gap"],
        "wishes": counts["wishes"],
        "terms_not_in_the_list": counts["uncatalogued"],
        "coverage": counts["coverage"],
    }


def _evidence(args):
    if args.offset < 0 or args.batch < 1:
        raise Fail("--batch must be at least 1 and --offset at least 0")
    return claims.evidence(period.resolve(args.period), args.batch, args.offset, args.max_items)


def _claims(args):
    return claims.accept(read_json_arg(args.file))


def _brief(args):
    return brief.build(period.resolve(args.period))


def _check(args):
    return cite.check(period.resolve(args.period), read_json_arg(args.file))


def _show(args):
    chosen = period.resolve(args.period)
    store.load_report(chosen)
    return {"period": chosen.label(), "markdown": state.read_text(store.report_path(chosen.label(), "md"), "")}


def _list(args):
    return {"reports": report.list_reports()}


def _render(args):
    return report.render(period.resolve(args.period))


def register(groups):
    commands = group(groups, "trends", "Market trends: collect, count, write cited points, render the report.")
    period_help = "2026-W46, 2026-M11, 2026-Q4, 2026, or week | month | quarter | year for the current one"

    add = command(commands, "source-add", _source_add, "Add a trend source (a feed, a page, a person to follow).")
    add.add_argument("--kind", required=True, choices=store.KINDS)
    add.add_argument("--url", help="the feed or page; for a person: an optional feed or page of their own")
    add.add_argument("--name", help="a label; for a person: who to follow (required)")
    command(commands, "source-list", _source_list, "The trend sources and how many items each has stored.")
    remove = command(commands, "source-remove", _source_remove, "Remove a trend source (its items stay).")
    remove.add_argument("id", type=int)
    enable = command(commands, "source-enable", _source_enable, "Turn a trend source on (or, with --off, off) for the next run.")
    enable.add_argument("id", type=int)
    enable.add_argument("--off", action="store_true")

    def with_period(name, run, help_text):
        parser = command(commands, name, run, help_text)
        parser.add_argument("--period", required=True, help=period_help)
        return parser

    with_period("collect", _collect, "Read every enabled source and store what is new.")
    with_period("counts", _counts, "The code's numbers: skill counts, shares, directions, the gap, completeness.")
    evidence = with_period("evidence", _evidence, "Step 1: the next batch of items to read, inside <item> markers.")
    evidence.add_argument("--batch", type=int, default=6, help="items per batch (default 6)")
    evidence.add_argument("--offset", type=int, default=0, help="skip this many of the items still to read")
    evidence.add_argument("--max-items", type=int, default=None, help="read at most this many items for the report")
    accept = command(commands, "claims", _claims, "Step 1: check the topics and claims you wrote; keep the quoted ones.")
    accept.add_argument("--file", required=True, help="JSON file (or - for stdin)")
    with_period("brief", _brief, "Step 2: background, the code's numbers, wishes and the evidence to write points from.")
    check = with_period("check", _check, "Step 2: check the points you wrote and save the report.")
    check.add_argument("--file", required=True, help="JSON file (or - for stdin)")
    with_period("show", _show, "The saved report as Markdown.")
    command(commands, "list", _list, "The saved reports.")
    with_period("render", _render, "The saved report as a PDF (Markdown + HTML without Typst).")
