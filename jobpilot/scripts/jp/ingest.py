"""Turn a document, a posting file or a web page into clean text.

`.md` / `.txt` are read as they are. A PDF goes through `pypdf` or the `pdftotext` tool when one is
there; when neither is, `NeedsReader` tells the agent to read the PDF itself and pass the text.
HTML is reduced to its main content with the standard library only.
"""
import html
import html.parser
import os
import re
import shutil
import subprocess
import urllib.parse

from .cli import Fail

MIN_CHARS = 20
MAX_FILE_BYTES = 5 * 1024 * 1024


class NeedsReader(Fail):
    """No PDF reader on this machine: the agent reads the file and passes its text."""


def clean(text):
    """Normalise line endings and whitespace: no trailing spaces, at most one blank line in a row,
    runs of spaces inside a line become one."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    out, blank = [], 0
    for line in text.split("\n"):
        line = line.rstrip()
        if not line:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(re.sub(r"(?<=\S) {2,}", " ", line))
    return "\n".join(out).strip("\n") + "\n" if any(out) else ""


def truncate(text, limit):
    """`(text, cut)`: at most `limit` characters, cut at a line end when one is near."""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    newline = head.rfind("\n")
    return (head[:newline] if newline > limit * 0.8 else head), True


# --- HTML ----------------------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe", "form", "button", "select"}
_CHROME = {"nav", "header", "footer", "aside"}
_BLOCK = {
    "p", "div", "section", "article", "main", "br", "tr", "table", "ul", "ol", "dl", "dt", "dd", "blockquote",
    "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figure", "figcaption", "address", "details", "summary",
}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}


class _Text(html.parser.HTMLParser):
    """Text of a page as light markdown (headings, list items, paragraphs). `main` collects what is
    inside <main>/<article> so a page's navigation and footer can be left out."""

    def __init__(self, keep_chrome):
        super().__init__(convert_charrefs=True)
        self.keep_chrome = keep_chrome
        self.parts, self.main = [], []
        self.skip = self.in_main = 0
        self.title = ""
        self._in_title = False

    def _emit(self, text):
        self.parts.append(text)
        if self.in_main:
            self.main.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _VOID and tag not in _BLOCK:
            return
        if self.skip or tag in _SKIP or (tag in _CHROME and not self.keep_chrome):
            if tag not in _VOID:
                self.skip += 1
            return
        if tag in ("main", "article"):
            self.in_main += 1
        if tag in _BLOCK:
            self._emit("\n\n" if tag != "br" else "\n")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("#" * int(tag[1]) + " ")
        elif tag == "li":
            self._emit("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        if self.skip:
            self.skip -= 1
            return
        if tag in _BLOCK:
            self._emit("\n\n")
        if tag in ("main", "article") and self.in_main:
            self.in_main -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self._emit(re.sub(r"\s+", " ", data))


def _markdown(parts):
    text = "".join(parts)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"^(#+|-)\s*$", "", text, flags=re.M)  # empty headings and list items
    return clean(text)


def html_to_text(source, keep_chrome=False):
    """The readable text of an HTML page or fragment: the <main>/<article> part when it holds most
    of the page, the whole body otherwise."""
    parser = _Text(keep_chrome)
    try:
        parser.feed(source)
        parser.close()
    except Exception:  # a broken page still gives what was read so far
        pass
    whole, main = _markdown(parser.parts), _markdown(parser.main)
    body = main if len(main) >= 400 and len(main) >= len(whole) * 0.3 else whole
    title = re.sub(r"\s+", " ", parser.title).strip()
    if title and body and title not in body and not keep_chrome and "<html" in source[:2000].lower():
        body = f"# {title}\n\n{body}"
    return body


def looks_like_html(text):
    head = text[:3000].lower()
    return "<html" in head or "<!doctype html" in head or bool(re.search(r"<(body|div|p|ul|h[1-6])[\s>]", head))


def html_links(source, base):
    """Every `<a href>` of a page as `(absolute url, link text)`, http(s) only, in page order."""
    links = []

    class Links(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.href, self.text = None, []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.href, self.text = dict(attrs).get("href"), []
            elif self.href is not None:
                self.text.append(" ")  # <h3>Title</h3><span>Where</span> must not run together

        def handle_data(self, data):
            if self.href is not None:
                self.text.append(data)

        def handle_endtag(self, tag):
            if tag == "a" and self.href is not None:
                try:
                    url = urllib.parse.urljoin(base, self.href.strip())
                except ValueError:
                    url = ""
                if url.startswith(("http://", "https://")):
                    links.append((urllib.parse.urldefrag(url)[0], re.sub(r"\s+", " ", "".join(self.text)).strip()))
                self.href = None
            elif self.href is not None:
                self.text.append(" ")

    try:
        parser = Links()
        parser.feed(source)
        parser.close()
    except Exception:
        pass
    return links


# --- PDF -----------------------------------------------------------------------------------------

def _pdf_links(reader):
    links = []
    for page in reader.pages:
        for annotation in page.get("/Annots") or []:
            try:
                action = annotation.get_object().get("/A") or {}
                uri = action.get("/URI")
            except Exception:
                continue
            if isinstance(uri, str) and uri.startswith(("http://", "https://")) and uri not in links:
                links.append(uri)
    return links


def pdf_text(path):
    """Text of a PDF, plus the addresses behind its links (so a CV's "GitHub" link is traceable)."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        pypdf = None
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(path)
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
            links = _pdf_links(reader)
        except Exception as err:
            raise Fail(f"the PDF could not be read: {type(err).__name__}")
        text = clean(text)
        if links and len(text) >= MIN_CHARS:
            text += "\nLinks in this document:\n" + "".join(f"- {link}\n" for link in links)
        return text
    tool = shutil.which("pdftotext")
    if tool:
        done = subprocess.run([tool, "-layout", path, "-"], capture_output=True, timeout=60)
        if done.returncode != 0:
            raise Fail("the PDF could not be read by pdftotext")
        return clean(done.stdout.decode("utf-8", errors="replace"))
    raise NeedsReader(
        "no PDF reader on this machine",
        hint="read the PDF yourself, copy its text exactly as written (do not summarise or fix it) into a "
             "text file, and pass that with --file; or `pip install pypdf`",
    )


# --- files and pages -----------------------------------------------------------------------------

def read_file(path):
    """Clean text of a .pdf, .md, .txt or .html file."""
    if not os.path.isfile(path):
        raise Fail(f"no such file: {path}")
    if os.path.getsize(path) > MAX_FILE_BYTES:
        raise Fail("the file is larger than 5 MB")
    extension = os.path.splitext(path)[1].lower()
    if extension == ".pdf":
        text = pdf_text(path)
    elif extension in (".md", ".txt", ".text", ".markdown", ".html", ".htm", ""):
        with open(path, encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
        text = html_to_text(raw) if extension in (".html", ".htm") or looks_like_html(raw) else clean(raw)
    else:
        raise Fail(f"{extension} files are not read", hint="use a .pdf, .md, .txt or .html file")
    if len(text.strip()) < MIN_CHARS:
        raise Fail("the file has no readable text", hint="a scanned PDF has no text: read it yourself and pass the text")
    return text


def read_page(url, http=None):
    """Clean text of a public web page. Raises `Fail` with a hint when the page needs a browser."""
    from . import http as net

    http = http or net.client()
    try:
        response = http.get(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
    except net.HttpError as err:
        hint = "read the page with your own browsing tool, or ask the user to paste the posting, then pass it with --file"
        if err.status in (401, 403, 406, 429, 503):
            raise Fail(f"the site refuses automated reads ({err.status})", hint=hint, needs_browser=True)
        raise Fail(str(err), hint=hint)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "pdf" in content_type or response.body[:5] == b"%PDF-":
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf") as temp:
            temp.write(response.body)
            temp.flush()
            return pdf_text(temp.name), response
    raw = response.text()
    return (html_to_text(raw) if looks_like_html(raw) else clean(raw)), response
