"""A page or PDF the user (or a feed entry, or a story) points at, read with plain HTTP and
cleaned like a job description. There is no browser here: a page that needs JavaScript yields too
little text and is reported as such; a login or bot wall (HTTP 401/403/429/503) is for the user."""
import tempfile

from .. import http as net
from .. import ingest
from ..cli import Fail
from . import config
from . import text as tx

# A page with less readable text than this is a shell (JavaScript app, paywall, error page).
MIN_TEXT_CHARS = 200
ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.5"


class Unreadable(Exception):
    """The address answered, but not with text that can be used."""


def fetch_text(url):
    """`(address after redirects, readable text)`. Raises `net.HttpError` or `Unreadable`."""
    response = net.client().get(url, ACCEPT)
    kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if kind == "application/pdf" or response.body[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as temp:
            temp.write(response.body)
            temp.flush()
            try:
                text = tx.clean(ingest.pdf_text(temp.name))
            except Fail as err:
                raise Unreadable(err.message)
    elif not kind or "html" in kind or "xml" in kind:
        text = tx.clean(ingest.html_to_text(response.text()))
    elif kind.startswith("text/"):
        text = tx.clean(response.text())
    else:
        raise Unreadable(f"unsupported content type `{kind}`")
    text = tx.cap(text, config.MAX_TEXT_CHARS)
    if len(text) < MIN_TEXT_CHARS:
        raise Unreadable(f"the page has too little readable text ({len(text)} characters; it may need a browser to render)")
    return response.url, text


def make_item(url, title_hint, published_at, text):
    """An item for `url`. The text starts with a `# title` line: the hint when there is one, else
    the heading the page already has, else its first line."""
    hint = (title_hint or "").strip() or None
    if text.startswith("# "):
        title = hint or tx.first_line(text, 140)
    elif hint:
        title, text = hint, f"# {hint}\n\n{text}"
    else:
        title = tx.first_line(text, 140)
    return {"url": url, "title": title or url, "published_at": published_at, "text": text}


def collect(ctx, source_url):
    """The user's page source: one item for its address (a page already collected is not read again)."""
    url = tx.normalize_url(source_url)
    if url is None:
        raise Unreadable("the source has no http(s) address")
    if url in ctx.known:
        return [], 0
    _, text = fetch_text(url)
    return [make_item(url, None, None, text)], 0
