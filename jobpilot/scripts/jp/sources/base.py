"""What every job-source adapter shares: the candidate shape, addresses, text helpers, paging, the
role pre-filter, and the one way an adapter talks to the network (`Net`, on top of `jp.http`).

Code picks every address. Text from a board or a page is data: it is never followed as a link
unless an adapter's own rule accepts it, and it never chooses a host for an API call.
"""
import ipaddress
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from .. import http as net
from .. import ingest
from ..cli import Fail

# Longest candidate snippet.
SNIPPET_CHARS = 280
# How long a listing an adapter loaded is reused inside one process.
LISTING_TTL = 300.0
# Statuses that mean "a bot check or a login wall", not "broken".
WALL_STATUSES = (401, 403, 406, 429, 503)

BROWSER_HINT = (
    "read the page with your own browsing tool and add the job by hand (pass the posting text with "
    "--file); never try to get around a login, captcha or block page: ask the user instead"
)

# Tests only: reads of an origin on the left go to the address on the right (a loopback fixture
# server), and the answer's address is mapped back. Never set by a command.
ORIGINS = {}


class NeedsBrowser(Fail):
    """A page that cannot be read without a browser: a bot check or login wall, or a page that
    JavaScript draws. The skill has no browser: the agent may read the page itself."""

    def __init__(self, message, hint=None, **extra):
        extra.setdefault("needs_browser", True)
        super().__init__(message, hint or BROWSER_HINT, **extra)


def within(what, call, *args):
    """`call(*args)`, with `what` put before the message of a `Fail` ("Greenhouse source: ...")."""
    try:
        return call(*args)
    except NeedsBrowser:
        raise
    except Fail as err:
        raise Fail(f"{what}: {err.message}", hint=err.hint, **err.extra)


# ------------------------------------------------------------------------------------- addresses

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
_HOST_FORBIDDEN = set(" #%/:<>?@[\\]^|")
_PATH_SAFE = "!$%&'()*+,-./:;=@[\\]_|~"
_QUERY_SAFE = "!$%&()*+,-./:;=?@[\\]^_`{|}~"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TRIM = "".join(chr(c) for c in range(0x21))


class Url:
    """An http(s) address read the way a browser reads it: lower-case host, default port dropped,
    dot segments (also percent-encoded ones) resolved, so a check made on it holds for the request.
    Any other scheme parses to a `Url` with only its `scheme` set, which nothing reads."""

    __slots__ = ("scheme", "username", "password", "host", "port", "path", "query", "fragment")

    def __init__(self, scheme, host="", port=None, path="/", query=None, fragment=None, username="", password=None):
        self.scheme, self.host, self.port, self.path = scheme, host, port, path
        self.query, self.fragment, self.username, self.password = query, fragment, username, password

    def __str__(self):
        out = self.scheme + "://"
        if self.username or self.password is not None:
            out += self.username + (":" + self.password if self.password else "") + "@"
        out += self.host
        if self.port is not None:
            out += f":{self.port}"
        out += self.path
        if self.query is not None:
            out += "?" + self.query
        if self.fragment is not None:
            out += "#" + self.fragment
        return out

    def __repr__(self):
        return f"Url({str(self)!r})"

    def __eq__(self, other):
        return isinstance(other, Url) and str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    @property
    def is_http(self):
        return self.scheme in _DEFAULT_PORTS and bool(self.host)

    def segments(self):
        """The path's segments that are not empty, as written (still percent-encoded)."""
        return [segment for segment in self.path.split("/") if segment]

    def query_pairs(self):
        return urllib.parse.parse_qsl(self.query or "", keep_blank_values=True)

    def replace(self, **changes):
        fields = {name: getattr(self, name) for name in self.__slots__}
        fields.update(changes)
        return Url(**fields)

    def without_fragment(self):
        return self if self.fragment is None else self.replace(fragment=None)

    def join(self, href):
        """`href` resolved against this address; None when the result is not an address."""
        try:
            return parse_url(urllib.parse.urljoin(str(self), href.strip(_TRIM)))
        except ValueError:
            return None


def _host(raw):
    """A host as the request will use it, or None for one a browser would refuse."""
    host = raw.lower()
    if not host:
        return None
    if host.startswith("["):
        try:
            return "[" + str(ipaddress.IPv6Address(host[1:-1])) + "]" if host.endswith("]") else None
        except ValueError:
            return None
    if any(ch in _HOST_FORBIDDEN or ord(ch) < 0x21 or ord(ch) == 0x7F for ch in host):
        return None
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    last = host.rstrip(".").rsplit(".", 1)[-1]
    if last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last):
        # A name that ends in a number is an IPv4 address: only the plain dotted form is read.
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    return host


def _path(raw):
    out = []
    segments = raw.split("/")
    if raw.startswith("/"):
        segments = segments[1:]
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lower = segment.lower()
        if lower in ("..", ".%2e", "%2e.", "%2e%2e"):
            if out:
                out.pop()
            if last:
                out.append("")
        elif lower in (".", "%2e"):
            if last:
                out.append("")
        else:
            out.append(segment)
    return "/" + "/".join(urllib.parse.quote(segment, safe=_PATH_SAFE) for segment in out)


def parse_url(raw):
    """`raw` as a `Url`, or None when it is not an absolute address."""
    if not isinstance(raw, str):
        return None
    text = raw.strip(_TRIM).replace("\t", "").replace("\n", "").replace("\r", "")
    found = _SCHEME.match(text)
    if not found:
        return None
    scheme = found.group(1).lower()
    rest = text[found.end():]
    if scheme not in _DEFAULT_PORTS:
        return Url(scheme, path=rest)
    fragment = None
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = None
    if "?" in rest:
        rest, query = rest.split("?", 1)
    rest = rest.replace("\\", "/").lstrip("/")
    slash = rest.find("/")
    authority, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash:])
    username, password = "", None
    if "@" in authority:
        userinfo, authority = authority.rsplit("@", 1)
        username, colon, secret = userinfo.partition(":")
        password = secret if colon else None
    port = None
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return None
        hostname, tail = authority[:end + 1], authority[end + 1:]
        if tail and not tail.startswith(":"):
            return None
        port_text = tail[1:]
    else:
        hostname, _, port_text = authority.partition(":")
    if port_text:
        if not (port_text.isascii() and port_text.isdigit()) or int(port_text) > 65535:
            return None
        port = int(port_text)
        if port == _DEFAULT_PORTS[scheme]:
            port = None
    host = _host(hostname)
    if host is None:
        return None
    if query is not None:
        query = urllib.parse.quote(query, safe=_QUERY_SAFE)
    if fragment is not None:
        fragment = urllib.parse.quote(fragment, safe=_QUERY_SAFE + "#'")
    return Url(scheme, host, port, _path(path), query, fragment, username, password)


def with_scheme(raw):
    """`raw`, with `https://` in front when it has no scheme."""
    return raw if "://" in raw else "https://" + raw


def last_segment(url):
    """The last path segment of an address (a posting's id on Lever and Ashby), or None."""
    parsed = parse_url(url) if isinstance(url, str) else url
    if parsed is None or not parsed.is_http:
        return None
    segments = parsed.segments()
    return segments[-1] if segments else None


def is_board_name(name):
    """A board name that is safe in an API path: `[A-Za-z0-9._-]`, not empty, not starting with a
    dot (`.`, `..` and anything dotted first would walk out of the path)."""
    return bool(name) and not name.startswith(".") and re.fullmatch(r"[A-Za-z0-9._-]+", name) is not None


_NO_BOARD = "no company board is set: enter the board URL or its short name"


def board_slug(raw, hosts):
    """The board name in a board URL (`https://boards.greenhouse.io/acme/jobs/1` -> `acme`) or a bare
    name. `hosts` are the domains a board URL may be on."""
    raw = (raw or "").strip()
    if not raw:
        raise Fail(_NO_BOARD)
    if "/" not in raw and ":" not in raw:
        if is_board_name(raw):
            return raw
        raise Fail(f"`{raw}` is not a board name")
    url = parse_url(with_scheme(raw))
    if url is None or not url.is_http:
        raise Fail(f"`{raw}` is not a board URL")
    if not any(url.host == host or url.host.endswith("." + host) for host in hosts):
        raise Fail(f"`{url.host}` is not one of {', '.join(hosts)}")
    # `boards.greenhouse.io/embed/job_board?for=acme`
    slug = next((value for key, value in url.query_pairs() if key == "for"), None)
    if slug is None:
        slug = next((segment for segment in url.segments() if segment != "embed"), None)
    if slug is None or not is_board_name(slug):
        raise Fail(f"the board name is missing in `{raw}`")
    return slug


def board_host(raw, suffixes):
    """`(company, suffix)` of a board that lives on its own subdomain (`https://acme.recruitee.com/o/x`
    -> `("acme", "recruitee.com")`) or of a bare name (the first suffix). The name becomes a host
    label in the address the adapter builds, so it is exactly one label of `[A-Za-z0-9_-]`,
    lower-cased."""
    raw = (raw or "").strip()
    if not raw:
        raise Fail(_NO_BOARD)

    def valid(label):
        return re.fullmatch(r"[A-Za-z0-9_-]+", label) is not None

    if not any(ch in raw for ch in "/:."):
        if valid(raw):
            return raw.lower(), suffixes[0]
        raise Fail(f"`{raw}` is not a board name")
    url = parse_url(with_scheme(raw))
    if url is None or not url.is_http:
        raise Fail(f"`{raw}` is not a board URL")
    for suffix in suffixes:
        if url.host.endswith("." + suffix):
            label = url.host[: -len(suffix) - 1]
            if valid(label):
                return label, suffix
            raise Fail(f"`{url.host}` is not a company board on {suffix}")
    forms = " or ".join(f"<company>.{suffix}" for suffix in suffixes)
    raise Fail(f"`{url.host}` is not {forms}")


# ------------------------------------------------------------------------------------------ text

def text_of(value):
    """A JSON string, trimmed; `""` for anything else."""
    return value.strip() if isinstance(value, str) else ""


def at(value, *keys):
    """`value[k1][k2]...` through dicts; None when a step is missing."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def html_text(fragment):
    """Readable text of an HTML fragment (job descriptions arrive as HTML)."""
    if not isinstance(fragment, str) or not fragment.strip():
        return ""
    return ingest.html_to_text(fragment, keep_chrome=True).strip()


def unescape_markup(escaped):
    """Markup whose angle brackets arrive escaped (`&lt;p&gt;`), as the Greenhouse API sends it."""
    return (
        escaped.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
    )


def one_line(text, limit=None):
    """`text` on one line, at most `limit` characters."""
    line = " ".join((text or "").split())
    if limit is not None and len(line) > limit:
        return line[:limit].rstrip() + "..."
    return line


def words(text):
    """Lower-case words of `text` (letters and digits)."""
    return re.findall(r"[^\W_]+", (text or "").lower())


# Words that say how senior or which kind of job it is, not what it is.
ROLE_NOISE = ("senior", "junior", "staff", "principal", "lead", "head", "the", "and", "for", "intern")


def title_fits_roles(title, roles):
    """Does a job title fit one of the wanted roles? It fits when it shares a meaningful word with a
    role (`engineer` matches `engineering`). No roles means everything fits. This only thins out
    big boards before the expensive steps; precision is the scoring's job."""
    wanted = {word for role in roles or [] for word in words(role) if len(word) >= 3 and word not in ROLE_NOISE}
    if not wanted:
        return True
    for word in words(title):
        for target in wanted:
            if word == target:
                return True
            if len(word) >= 5 and len(target) >= 5 and (word.startswith(target) or target.startswith(word)):
                return True
    return False


def mentions_lowered(text, term):
    """`mentions` for a `text` that is lower case already."""
    term = (term or "").strip().lower()
    if not term:
        return False
    start = text.find(term)
    while start >= 0:
        end = start + len(term)
        before = start == 0 or not text[start - 1].isalnum()
        after = end == len(text) or not text[end].isalnum()
        if before and after:
            return True
        start = text.find(term, start + 1)
    return False


def mentions(text, term):
    """Does `text` mention `term` as a word (`go` is not in `google`; `c++` and `.net` work)?"""
    return mentions_lowered((text or "").lower(), term)


def header(title, company, location):
    """The first lines of every posting text."""
    return f"# {title}\n\nCompany: {company}\nLocation: {location}"


# ---------------------------------------------------------------------------------------- paging

def candidate(url, title, company, location="", snippet=""):
    return {"url": url, "title": title, "company": company, "location": location, "snippet": snippet}


def roles_of(prefs):
    return [role for role in (prefs or {}).get("roles") or [] if isinstance(role, str)]


def bad_cursor(cursor):
    return Fail(f"the search position `{cursor}` is not valid", hint="start again without a cursor")


def page_of(items, cursor, page_size):
    """`(page, next cursor)`: the items from offset `cursor` (None is the start). The same cursor
    gives the same page."""
    start = 0
    if cursor is not None:
        cursor = str(cursor)
        if not (cursor.isascii() and cursor.isdigit()):
            raise bad_cursor(cursor)
        start = int(cursor)
    end = min(start + max(page_size, 1), len(items))
    return list(items[start:end]), (str(end) if end < len(items) else None)


def pair_cursor(cursor):
    """`"<a>:<b>"` as two numbers; `(0, 0)` for the start."""
    if cursor is None:
        return 0, 0
    parts = str(cursor).split(":")
    if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
        raise bad_cursor(cursor)
    return int(parts[0]), int(parts[1])


class Cached:
    """A value an adapter loaded and needs again within minutes. A failed load is not kept."""

    def __init__(self, ttl=LISTING_TTL):
        self.ttl, self.at, self.value = ttl, None, None

    def get(self, load):
        if self.at is not None and time.monotonic() - self.at < self.ttl:
            return self.value
        value = load()
        self.value, self.at = value, time.monotonic()
        return value


# --------------------------------------------------------------------------------------- network

def _route(url):
    for origin, target in ORIGINS.items():
        if url == origin or url.startswith(origin + "/") or url.startswith(origin + "?"):
            return target + url[len(origin):]
    return url


def _unroute(url):
    for origin, target in sorted(ORIGINS.items(), key=lambda pair: -len(pair[1])):
        if url == target or url.startswith(target + "/") or url.startswith(target + "?"):
            return origin + url[len(target):]
    return url


def http_fail(doing, err):
    """An `HttpError` as a `Fail` that names the step (`reading the Greenhouse board: ...`)."""
    extra = {"status": err.status} if err.status else {}
    if err.status == 404:
        hint = "nothing is at that address: check the source's address or name"
    elif err.status is None or err.status >= 500 or err.status == 429:
        hint = "a network or server problem: try again later"
    else:
        hint = None
    return Fail(f"{doing}: {err}", hint=hint, **extra)


class Net:
    """How adapters read: `jp.http`, with failures turned into `Fail`s that name the step."""

    def __init__(self, http=None):
        self._http = http

    @property
    def http(self):
        return self._http or net.client()

    def get_json(self, url, doing):
        try:
            return self.http.get_json(_route(url))
        except net.HttpError as err:
            raise http_fail(doing, err)

    def get_text(self, url, accept, doing):
        try:
            return self.http.get_text(_route(url), accept)
        except net.HttpError as err:
            raise http_fail(doing, err)

    def post_json(self, url, payload, doing):
        try:
            return self.http.post_json(_route(url), payload)
        except net.HttpError as err:
            raise http_fail(doing, err)

    def get(self, url, accept):
        """The response of a page read, its `url` the address after redirects. A wall is
        `NeedsBrowser`; any other failure a `Fail`."""
        host = urllib.parse.urlsplit(url).hostname or "the page"
        try:
            response = self.http.get(_route(url), accept)
        except net.HttpError as err:
            if err.status in WALL_STATUSES:
                raise NeedsBrowser(
                    f"{host} refuses automated reads (HTTP {err.status}: a bot check or a login wall)", status=err.status
                )
            raise http_fail(f"fetching {host}", err)
        response.url = _unroute(response.url)
        return response


class Adapter:
    """A place to find postings. `search` gives one page of candidates; `posting` the full text when
    the adapter has it without loading the posting's web page."""

    def __init__(self, http=None):
        self.net = http if isinstance(http, Net) else Net(http)

    def search(self, prefs, cursor=None, page_size=25):
        raise NotImplementedError

    def posting(self, candidate):
        return None

    def board_hints(self):
        return []

    def _fitting_page(self, candidates, prefs, cursor, page_size):
        roles = roles_of(prefs)
        fitting = [dict(c) for c in candidates if title_fits_roles(c["title"], roles)]
        page, following = page_of(fitting, cursor, page_size)
        return {"candidates": page, "next": following}


# ------------------------------------------------------------------------------------------- XML

class XmlError(ValueError):
    def __init__(self, message, deep=False):
        super().__init__(message)
        self.deep = deep


def local_name(tag):
    """An element name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def xml_events(text, max_depth=64):
    """`(event, element, depth)` for every start and end of an element, read in pieces so a file
    that nests too deep is refused after its first piece. The root's depth is 1. Entity
    declarations are not read at all."""
    if "<!ENTITY" in text:
        raise XmlError("entity declarations are not read")
    parser = ET.XMLPullParser(("start", "end"))
    depth = 0
    try:
        for start in range(0, len(text) + 1, 16384):
            piece = text[start:start + 16384]
            if piece:
                parser.feed(piece)
            else:
                parser.close()
            for event, element in parser.read_events():
                if event == "start":
                    if depth >= max_depth:
                        raise XmlError("its elements nest too deep", deep=True)
                    depth += 1
                    yield event, element, depth
                else:
                    yield event, element, depth
                    depth -= 1
            if not piece:
                return
        parser.close()
    except ET.ParseError as err:
        raise XmlError(str(err))
