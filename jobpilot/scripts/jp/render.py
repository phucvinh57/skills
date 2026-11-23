"""A checked CV -> PDF (Typst, `assets/cv.typ`) or, without Typst, a print-ready HTML file.

The agent writes plain text. In a tailored CV the markup fields (summary lines, bullets, group
titles, project intros) may use `**bold**`, `_italic_` and `[text](https://url)`; nothing else has
a meaning. This module turns that into Typst markup (everything else escaped, so a CV renders
exactly the words that were checked) and into HTML (escaped).
"""
import html
import os
import re

from . import cvmodel, state, typst

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "cv.typ")
DEFAULT_TITLES = {
    "summary": "Summary", "skills": "Skills", "experience": "Experience", "projects": "Projects",
    "education": "Education", "certifications": "Certifications", "languages": "Languages",
}
DEFAULT_STYLE = {"accent-color": "#0092b8", "font": "New Computer Modern", "font-size": "10pt", "paper": "a4"}

# ---- the markup the agent may write --------------------------------------------------------------

_LINK = re.compile(r'\[([^\[\]]+)\]\((https?://[^\s()<>"\\]+)\)')
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![A-Za-z0-9_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![A-Za-z0-9_])")


def parse_inline(text, links=True):
    """`text` as nodes: `("text", s)`, `("bold", nodes)`, `("italic", nodes)`, `("link", nodes, url)`."""
    nodes, position = [], 0
    patterns = (("link", _LINK), ("bold", _BOLD), ("italic", _ITALIC)) if links else (("bold", _BOLD), ("italic", _ITALIC))
    while position < len(text):
        best = None
        for kind, pattern in patterns:
            match = pattern.search(text, position)
            if match and (best is None or match.start() < best[1].start()):
                best = (kind, match)
        if best is None:
            break
        kind, match = best
        if match.start() > position:
            nodes.append(("text", text[position:match.start()]))
        if kind == "link":
            nodes.append(("link", parse_inline(match.group(1), links=False), match.group(2)))
        else:
            nodes.append((kind, parse_inline(match.group(1), links)))
        position = match.end()
    if position < len(text):
        nodes.append(("text", text[position:]))
    return nodes


def link_targets(text):
    """The address of every `[text](url)` in a markup field."""
    return [match.group(2) for match in _LINK.finditer(text)]


def without_link_targets(text):
    """A markup field as the reader sees its links: the text, not the address."""
    return _LINK.sub(lambda match: match.group(1), text)


# ---- Typst --------------------------------------------------------------------------------------

# Characters with a meaning in Typst markup, escaped anywhere.
_SPECIAL = "\\#$@*_<>[]`~"
# Characters that start a heading, list, enum or term only at the start of a line.
_LINE_START = "=-+/"


def escape(text, at_start=True):
    """`text` as Typst markup that shows exactly `text`. `at_start`: nothing but blanks comes before
    it on the line, so a leading `-`, `=`, `+`, `/` or `1.` would start a list or a heading."""
    out = []
    blank_so_far = at_start
    for i, c in enumerate(text):
        following = text[i + 1] if i + 1 < len(text) else ""
        needs_escape = (
            c in _SPECIAL
            or (c == "/" and following in ("/", "*"))  # `//` and `/*` start comments
            or (blank_so_far and c in _LINE_START)
            # `1. ` at the start is a numbered list item
            or (at_start and c == "." and i > 0 and all(d in "0123456789" for d in text[:i]) and following.isspace())
        )
        if needs_escape:
            out.append("\\")
        out.append(c)
        if not c.isspace():
            blank_so_far = False
    return "".join(out)


def _typst(nodes):
    out = ""
    for node in nodes:
        if node[0] == "text":
            out += escape(node[1], at_start=not out.strip())
        elif node[0] == "bold":
            out += "#strong[" + _typst(node[1]) + "];"
        elif node[0] == "italic":
            out += "#emph[" + _typst(node[1]) + "];"
        else:
            address = node[2].replace("\\", "\\\\").replace('"', '\\"')
            out += '#link("' + address + '")[' + _typst(node[1]) + "];"
    return out


def to_typst(text):
    """A markup field as Typst markup."""
    return _typst(parse_inline(" ".join(text.split())))


def _html(nodes):
    out = ""
    for node in nodes:
        if node[0] == "text":
            out += html.escape(node[1])
        elif node[0] == "bold":
            out += "<strong>" + _html(node[1]) + "</strong>"
        elif node[0] == "italic":
            out += "<em>" + _html(node[1]) + "</em>"
        else:
            out += '<a href="' + html.escape(node[2], quote=True) + '">' + _html(node[1]) + "</a>"
    return out


def to_html(text):
    """A markup field as HTML."""
    return _html(parse_inline(" ".join(text.split())))


# ---- the data both renderers read ---------------------------------------------------------------

def _convert(cv, convert):
    """A copy of `cv` with every markup field passed through `convert`, hidden items removed,
    `sections`, `titles` and `style` filled in."""
    lines = lambda items: [convert(item) for item in items if item.strip()]  # noqa: E731
    visible = lambda items: [dict(item) for item in items if not item.get("hidden")]  # noqa: E731
    data = {
        "basics": dict(cv["basics"]),
        "style": {**DEFAULT_STYLE, **cv["style"]},
        "titles": {**DEFAULT_TITLES, **{key: value for key, value in cv["titles"].items() if value.strip()}},
        "summary": lines(cv["summary"]),
        "skills": [group for group in visible(cv["skills"]) if group["items"]],
        "experience": visible(cv["experience"]),
        "projects": visible(cv["projects"]),
        "education": visible(cv["education"]),
        "certifications": visible(cv["certifications"]),
        "languages": visible(cv["languages"]),
    }
    for entry in data["experience"]:
        entry["bullets"] = lines(entry["bullets"])
        entry["groups"] = [
            {"title": convert(group["title"]), "bullets": lines(group["bullets"])} for group in entry["groups"]
        ]
    for entry in data["projects"]:
        entry["intro"] = convert(entry["intro"]) if entry["intro"].strip() else ""
        entry["bullets"] = lines(entry["bullets"])
    for entry in data["education"]:
        entry["bullets"] = lines(entry["bullets"])
    order = cv["sections"] or list(cvmodel.SECTIONS)
    data["sections"] = [name for i, name in enumerate(order) if data[name] and name not in order[:i]]
    return data


def typst_data(cv):
    """What `assets/cv.typ` reads: the CV with its markup fields as Typst markup."""
    return _convert(cv, to_typst)


# ---- HTML ---------------------------------------------------------------------------------------

def bare_url(url, handle_prefix=""):
    """A profile link as `host/path`: scheme, `www.` and trailing `/` dropped; a bare handle
    (`annlee`, `@annlee`) gets the site's prefix (`github.com/`)."""
    url = url.strip()
    for prefix in ("https://", "http://", "www."):
        if url.lower().startswith(prefix):
            url = url[len(prefix):]
    url = url.rstrip("/")
    if handle_prefix and url and "/" not in url and "." not in url:
        url = handle_prefix + url.lstrip("@")
    return url


def _dates(entry):
    start, end = entry.get("start", "").strip(), entry.get("end", "").strip()
    return html.escape(f"{start} — {end}" if start and end else start or end)


def _list(items):
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""


def _row(left, right):
    return f'<div class="row"><span>{left}</span><span class="right">{right}</span></div>'


def _site(url, text=None):
    bare = bare_url(url)
    if not bare:
        return html.escape(text or "")
    return f'<a href="https://{html.escape(bare, quote=True)}">{html.escape(text or bare)}</a>'


_CSS = """
@page { size: %(paper)s; margin: 0.5in; }
* { box-sizing: border-box; }
body { font-family: "New Computer Modern", "Latin Modern Roman", "CMU Serif", Georgia, "Times New Roman", serif;
  font-size: %(size)s; line-height: 1.3; color: #000; max-width: 7.27in; margin: 0.5in auto; padding: 0 0.2in; }
@media print { body { margin: 0; padding: 0; max-width: none; } }
a { color: %(accent)s; }
h1 { font-size: 20pt; margin: 0; color: %(accent)s; }
h2 { font-size: 1.2em; font-variant: small-caps; color: %(accent)s; border-bottom: 1pt solid #000;
  margin: 0.9em 0 0.45em; padding-bottom: 0.1em; }
p { margin: 0.35em 0; }
ul { margin: 0.35em 0; padding-left: 1.3em; }
li { margin: 0.2em 0; text-align: justify; }
.contact { margin: 0.3em 0 0; }
.row { display: flex; justify-content: space-between; gap: 1em; }
.right { text-align: right; white-space: nowrap; }
.entry { margin-top: 0.55em; break-inside: avoid; }
"""


def html_page(cv):
    """The CV as one self-contained, print-ready HTML page (no remote asset)."""
    data = _convert(cv, to_html)
    b, style = data["basics"], data["style"]
    text = lambda value: html.escape(value.strip())  # noqa: E731
    contact = [text(b["headline"])]
    if b["phone"].strip():
        contact.append(f'<a href="tel:{html.escape(b["phone"].replace(" ", ""), quote=True)}">{text(b["phone"])}</a>')
    contact.append(text(b["location"]))
    if b["email"].strip():
        contact.append(f'<a href="mailto:{html.escape(b["email"].strip(), quote=True)}">{text(b["email"])}</a>')
    for key, prefix in (("github", "github.com/"), ("linkedin", "linkedin.com/in/"), ("website", "")):
        contact.append(_site(bare_url(b[key], prefix)))
    parts = [f"<h1>{text(b['name'])}</h1>", '<p class="contact">' + " &nbsp;|&nbsp; ".join(c for c in contact if c) + "</p>"]

    def section(name):
        if name == "summary":
            return _list(data["summary"])
        if name == "skills":
            return _list(f"<strong>{text(g['category'])}</strong>: {html.escape(', '.join(g['items']))}" for g in data["skills"])
        if name == "languages":
            return _list(f"<strong>{text(x['language'])}</strong>: {text(x['fluency'])}" for x in data["languages"])
        out = []
        for e in data[name]:
            if name == "experience":
                head = _row(f"<strong>{text(e['position'])}</strong>", _dates(e))
                head += _row(_site(e["url"], e["company"].strip()), f"<em>{text(e['location'])}</em>")
                body = _list(e["bullets"]) + "".join(f"<p><em>{g['title']}</em></p>" + _list(g["bullets"]) for g in e["groups"])
            elif name == "projects":
                link = f" ({_site(e['url'])})" if bare_url(e["url"]) else ""
                left = f"<strong>{text(e['role'])}</strong>, {text(e['name'])}" if e["role"].strip() else f"<strong>{text(e['name'])}</strong>"
                head = _row(left + link, _dates(e))
                body = (f"<p>{e['intro']}</p>" if e["intro"] else "") + _list(e["bullets"])
            elif name == "education":
                head = _row(f"<strong>{text(e['institution'])}</strong>", _dates(e))
                degree = text(e["degree"]) + (f" (GPA: {text(e['gpa'])})" if e["gpa"].strip() else "")
                head += _row(f"<em>{degree}</em>", f"<em>{text(e['location'])}</em>")
                body = _list(e["bullets"])
            else:  # certifications
                link = f" ({_site(e['url'])})" if bare_url(e["url"]) else ""
                issuer = f", {text(e['issuer'])}" if e["issuer"].strip() else ""
                head, body = _row(f"<strong>{text(e['name'])}</strong>{issuer}{link}", text(e["date"])), ""
            out.append(f'<div class="entry">{head}{body}</div>')
        return "".join(out)

    for name in data["sections"]:
        parts.append(f"<h2>{html.escape(data['titles'][name])}</h2>" + section(name))
    css = _CSS % {"paper": "letter" if style["paper"] == "us-letter" else "A4", "size": style["font-size"],
                  "accent": style["accent-color"]}
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{text(b['name'])}</title>\n<style>{css}</style>\n</head>\n<body>\n" + "\n".join(parts) + "\n</body>\n</html>\n"
    )


# ---- files --------------------------------------------------------------------------------------

def render_job(slug, cv):
    """Render a checked CV into the job's folder.

    With Typst: `cv.pdf` + `page-N.png` -> `{"pdf", "pages", "page_count"}`. Without: `cv.html` ->
    `{"html", "pages": [], "page_count": None}`. Raises `typst.TypstError` when Typst refuses it."""
    folder = os.path.join("jobs", slug)
    state.job_dir(slug)  # refuses anything that is not a job id
    if not typst.available():
        return {"html": state.write_text(os.path.join(folder, "cv.html"), html_page(cv)), "pages": [], "page_count": None}
    with open(TEMPLATE, encoding="utf-8") as handle:
        state.write_text(os.path.join(folder, "cv.typ"), handle.read())
    state.write_json(os.path.join(folder, "cv.render.json"), typst_data(cv))
    done = typst.compile_pdf(state.job_dir(slug), "cv.typ", "cv.render.json", "cv.pdf")
    return {"pdf": done["pdf"], "pages": done["pages"], "page_count": done["page_count"]}
