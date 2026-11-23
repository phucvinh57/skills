"""Market trends: collect items (Hacker News, the YC directory, feeds, pages, people to follow),
count in code, let the agent write points, enforce in code that every point cites a stored item.

    period    the week, month, quarter or year a report covers (one report per period)
    config    endpoints and every limit
    text      small text helpers shared by the adapters and the checks
    store     trends/sources.json, items.json, claims.json, state.json, reports/
    hackernews, yc, rss, page, person   one adapter per source kind (network through `jp.http` only)
    collect   run every enabled source; one failing source never stops the others
    frame     MarketFrame: background + place, never wishes; decides what is counted
    stats     the statistical tests behind a direction
    count     skill counts per month, directions, the gap, wishes, coverage
    terms     words the skill list does not know, per kind of item
    pick      which items the agent reads
    claims    step 1: evidence batches out, verified claims in
    brief     step 2: what the agent sees before it writes the points
    cite      every point is checked; the report is saved
    report    Markdown, HTML and the JSON `assets/trends.typ` reads
"""
