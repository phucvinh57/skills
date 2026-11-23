"""A saved report as Markdown, as the JSON `assets/trends.typ` reads, as a PDF, and (without
Typst) as a self-contained HTML page. Text the agent or a third party wrote is always escaped, and
the only links are the stored http(s) addresses of the cited items."""
import html
import os
import urllib.parse

from .. import state, typst
from . import config, count, stats, store
from . import period as clock
from . import text as tx

TABLE_SKILLS = 10   # skills in the Markdown table
PDF_SKILLS = 12     # skills in the PDF's table
LEFT_OUT = "point(s) were left out: no valid source, a quote that is not in the source, or a claim against the app's numbers."
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "assets", "trends.typ")


def escape_md(text):
    """Text safe inside Markdown: one line, and nothing in it can become emphasis, a link, an
    image, HTML or a heading. An address in it is broken up too (`https\\://`, `www\\.`, `\\@`)."""
    line = tx.one_line(text)
    out = []
    for at, char in enumerate(line):
        before = line[:at]
        if char in "\\`*_[]<>|~&@":
            escape = True
        elif char == ":":
            escape = line[at + 1:at + 3] == "//"
        elif char == ".":
            escape = before[-3:].lower() == "www" or (at > 0 and before.isascii() and before.isdigit())
        elif char in "#-+":
            escape = at == 0
        else:
            escape = False
        out.append("\\" + char if escape else char)
    return "".join(out)


def link_destination(url):
    """A stored address as a link destination: only http(s), and with the characters that could
    end the link or start markup percent-encoded. None for anything else."""
    normal = tx.normalize_url(url)
    if normal is None:
        return None
    safe = urllib.parse.quote(normal, safe="%/:?#@!$&+,;=-._~")
    return safe


def _gap_scope(counts):
    return "local" if counts.get("gap_scope") == "local" else "global"


def _term_groups(counts, escape):
    groups = []
    for stratum in count.STRATA:
        found = [f"{escape(t['term'])} ({t['items']}{', rising' if t['rising'] else ''})"
                 for t in counts.get("uncatalogued") or [] if t.get("stratum") == stratum]
        if found:
            groups.append(f"{count.STRATUM_PLURAL[stratum]}: {', '.join(found)}")
    return groups


def render_markdown(period, data):
    """The readable report: sections of points, each followed by its sources as links and the quote
    under it; the table of the most mentioned skills with the coverage of the counts; and what the
    code computed for the user (the gap by bucket, the wishes against the market, the terms not in
    the list)."""
    url_of = {item["id"]: item["url"] for item in data["items"]}
    md = f"# {clock.report_title(period)}\n\n"
    for section in config.SECTIONS:
        points = [point for point in data["points"] if point["section"] == section]
        if not points:
            continue
        md += f"## {config.SECTION_TITLES[section]}\n\n"
        for point in points:
            md += f"- **{escape_md(point['topic'])}**: {escape_md(point['text'])}" if point["topic"].strip() else f"- {escape_md(point['text'])}"
            links = []
            for item_id in point["citations"]:
                destination = link_destination(url_of.get(item_id, ""))
                links.append(f"[{item_id}]({destination})" if destination else f"[{item_id}]")
            md += f" (sources: {', '.join(links)})\n"
            if point["quote"].strip():
                md += f"  > {escape_md(point['quote'])}\n"
        md += "\n"
    if not data["points"]:
        md += "No point could be tied to a source this time.\n\n"
    counts = data["counts"]
    months = counts.get("months") or []
    target_months = counts.get("target_months", 0)
    if counts.get("skills"):
        md += ("## Skill frequency\n\nDocuments mentioning the skill, per month (counted by the app). The last column is "
               "the share of the postings of the period's own months, with its 95% range.\n\n| Skill |")
        md += "".join(f" {month} |" for month in months) + " Total | Direction | This period (95% range) |\n|---|"
        md += "---|" * (len(months) + 3) + "\n"
        for skill in counts["skills"][:TABLE_SKILLS]:
            level = stats.level_text(target_months, skill["target_permille"], skill["low_permille"], skill["high_permille"])
            md += f"| {escape_md(skill['skill'])} |" + "".join(f" {n} |" for n in skill["by_month"])
            md += f" {skill['total']} | {skill['direction']} | " + (f"{level[0]} ({level[1]})" if level else "-") + " |\n"
        md += "\n"
    if target_months > 0:
        md += "Coverage: " + " ".join(count.coverage_lines(counts.get("coverage") or {}, months)) + "\n\n"
    if counts.get("gap"):
        md += (f"## Gap, sorted by the app\n\nSkills of the {_gap_scope(counts)} postings of your field against your profile; "
               "share of those postings over the window.\n\n")
        for bucket, label in (("build_on", "Build on"), ("learn_next", "Learn next"), ("at_risk", "At risk")):
            skills = [f"{escape_md(g['skill'])} ({g['direction']}, {stats.permille_text(g['share_permille'])})"
                      for g in counts["gap"] if g["bucket"] == bucket]
            if skills:
                md += f"- {label}: {', '.join(skills)}\n"
        md += "\n"
    if counts.get("wishes"):
        md += "## Wishes against the market, measured by the app\n\n| Wish | Kind | Avoid? | Direction | Mentions |\n|---|---|---|---|---|\n"
        for wish in counts["wishes"]:
            direction = wish["direction"] if wish["mentions"] else "no data"
            md += f"| {escape_md(wish['wish'])} | {wish['kind']} | {'yes' if wish['avoided'] else 'no'} | {direction} | {wish['mentions']} |\n"
        md += "\n"
    groups = _term_groups(counts, escape_md)
    if groups:
        md += ("Terms not in the list (documents naming them, by kind of item; rising: tested against the period before "
               "and kept after the control of false discoveries): " + "; ".join(groups) + "\n\n")
    if data.get("dropped"):
        md += f"_{data['dropped']} {LEFT_OUT}_\n"
    return md


def export_json(report):
    """The data `assets/trends.typ` reads: the sections in display order (empty ones left out),
    each point with its citations numbered by first use, the most mentioned skills, the coverage of
    the counts (one sentence per fact) and the numbered sources. Text only."""
    data = report["data"]
    items = {item["id"]: item for item in data["items"]}
    numbers, sources, sections = {}, [], []
    for section in config.SECTIONS:
        points = []
        for point in data["points"]:
            if point["section"] != section:
                continue
            cites = []
            for item_id in point["citations"]:
                item = items.get(item_id)
                if item is None:
                    continue
                if item_id not in numbers:
                    numbers[item_id] = len(sources) + 1
                    sources.append({"n": numbers[item_id], "title": tx.one_line(item["title"]),
                                    "url": link_destination(item["url"].strip()) or ""})
                if numbers[item_id] not in cites:
                    cites.append(numbers[item_id])
            points.append({"topic": tx.one_line(point["topic"]), "text": tx.one_line(point["text"]),
                           "quote": tx.one_line(point["quote"]), "cites": cites})
        if points:
            sections.append({"title": config.SECTION_TITLES[section], "points": points})
    counts = data["counts"]
    months = counts.get("months") or []
    skills = [{"skill": tx.one_line(skill["skill"]), "total": skill["total"], "direction": skill["direction"],
               "counts": [{"month": month, "count": n} for month, n in zip(months, skill["by_month"])]}
              for skill in (counts.get("skills") or [])[:PDF_SKILLS]]
    span = f"{months[0]} to {months[-1]}" if months and months[0] != months[-1] else (months[0] if months else "")
    created = tx.from_iso(report.get("created_at")) or clock.now()
    return {
        "title": tx.one_line(report["title"]),
        "period_title": clock.period_title(report["period"]),
        "generated": clock.long_date(created),
        "items_read": data.get("items_read", 0),
        "dropped": data.get("dropped", 0),
        "sections": sections,
        "months": span,
        "skills": skills,
        "coverage": count.coverage_lines(counts.get("coverage") or {}, months) if counts.get("target_months", 0) > 0 else [],
        "sources": sources,
    }


def render_html(view):
    """The report as one self-contained HTML page (no script, no remote file), from `export_json`."""
    e = html.escape
    marks = {str(source["n"]): source["url"] for source in view["sources"]}

    def cites(numbers):
        out = []
        for n in numbers:
            url = marks.get(str(n), "")
            out.append(f'<a href="{e(url)}">[{n}]</a>' if url else f"[{n}]")
        return " <small>" + "".join(out) + "</small>" if out else ""

    body = [f"<p class=kicker>Market insight</p><h1>{e(view['title'])}</h1>",
            f"<p class=meta>{e(view['period_title'])} · generated {e(view['generated'])} · {view['items_read']} items read</p>"]
    if not view["sections"]:
        body.append("<p class=meta>No point could be tied to a source this time.</p>")
    for section in view["sections"]:
        body.append(f"<h2>{e(section['title'])}</h2>")
        for point in section["points"]:
            topic = f"<strong>{e(point['topic'])}: </strong>" if point["topic"] else ""
            quote = f"<blockquote>“{e(point['quote'])}”</blockquote>" if point["quote"] else ""
            body.append(f"<div class=point><p>{topic}{e(point['text'])}{cites(point['cites'])}</p>{quote}</div>")
    if view["skills"]:
        body.append(f"<h2>Skill frequency</h2><p class=meta>Documents mentioning the skill, {e(view['months'])} "
                    "(counted by the app, not by the model).</p><table><tr><th>Skill</th><th>Total</th><th>By month</th><th>Direction</th></tr>")
        for skill in view["skills"]:
            by_month = " ".join(str(c["count"]) for c in skill["counts"])
            body.append(f"<tr><td>{e(skill['skill'])}</td><td class=n>{skill['total']}</td><td>{by_month}</td>"
                        f"<td class={e(skill['direction'])}>{e(skill['direction'])}</td></tr>")
        body.append("</table>")
    if view["coverage"]:
        body.append(f"<p class=meta>{e(' '.join(view['coverage']))}</p>")
    if view["dropped"]:
        body.append(f"<p class=meta><em>{view['dropped']} {e(LEFT_OUT)}</em></p>")
    if view["sources"]:
        body.append("<h2>Sources</h2><ol>")
        for source in view["sources"]:
            link = f'<br><small><a href="{e(source["url"])}">{e(source["url"])}</a></small>' if source["url"] else ""
            body.append(f"<li>{e(source['title'])}{link}</li>")
        body.append("</ol>")
    style = ("body{font:15px/1.5 system-ui,sans-serif;color:#1d2433;max-width:46rem;margin:2rem auto;padding:0 1rem}"
             "h1{margin:.1em 0}h2{font-size:1.15rem;margin-top:1.6em}.kicker,.meta{color:#5b6475;font-size:.85rem}"
             ".kicker{text-transform:uppercase;letter-spacing:.08em;margin:0}blockquote{margin:.3em 0 0;padding-left:.6em;"
             "border-left:2px solid #d9dde5;color:#5b6475;font-style:italic}a{color:#2f5bd3}table{border-collapse:collapse}"
             "td,th{padding:.2em .7em;text-align:left}th{color:#5b6475;font-size:.85rem}.n{text-align:right}"
             ".rising{color:#1f7a52}.falling{color:#b5452a}.point{margin-bottom:.7em}.point p{margin:0}")
    return (f"<!doctype html>\n<html lang=en><head><meta charset=utf-8><title>{e(view['title'])}</title>"
            f"<style>{style}</style></head><body>\n" + "\n".join(body) + "\n</body></html>\n")


def render(period, pages=False):
    """The saved report of `period` as a PDF through `assets/trends.typ`; without Typst, as an HTML
    page next to the Markdown, with the hint to install it."""
    report = store.load_report(period)
    label = period.label()
    view = export_json(report)
    if not typst.available():
        target = state.write_text(store.report_path(label, "html"), render_html(view))
        return {"period": label, "pdf": None, "html": target, "markdown": state.path(store.report_path(label, "md")),
                "note": "Typst is not installed, so there is no PDF: the report was written as Markdown and HTML.",
                "hint": typst.INSTALL_HINT}
    with open(TEMPLATE, encoding="utf-8") as handle:
        state.write_text("trends/reports/trends.typ", handle.read())
    state.write_json(f"trends/reports/{label}.typ.json", view)
    done = typst.compile_pdf(state.path("trends", "reports"), "trends.typ", f"{label}.typ.json", f"{label}.pdf", pages=pages)
    out = {"period": label, "pdf": done["pdf"], "markdown": state.path(store.report_path(label, "md")),
           "sources": len(view["sources"])}
    if pages:
        out["pages"] = done["pages"]
    return out


def list_reports():
    """The saved reports, newest period first."""
    root = state.path("trends", "reports")
    found = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not name.endswith(".json") or name.endswith(".typ.json"):
            continue
        period = clock.Period.parse(name[:-len(".json")])
        report = state.read_json(f"trends/reports/{name}") if period else None
        if not report or not isinstance(report.get("data"), dict):
            continue
        found.append({
            "period": period.label(), "kind": period.kind(), "title": report.get("title"), "created_at": report.get("created_at"),
            "points": len(report["data"].get("points") or []), "dropped": report["data"].get("dropped", 0),
            "pdf": os.path.isfile(os.path.join(root, f"{period.label()}.pdf")), "_start": period.start(),
        })
    found.sort(key=lambda entry: (entry["_start"], entry["period"]), reverse=True)
    for entry in found:
        del entry["_start"]
    return found
