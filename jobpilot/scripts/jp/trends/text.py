"""Small text helpers shared by the adapters and the checks: HTML fragments to plain text, dates,
titles, length caps, whole-word matching."""
import datetime
import email.utils
import re

from ..ingest import clean as _clean

UTC = datetime.timezone.utc

_BLOCKS = re.compile(r"<\s*(?:br|/p|p|/div|div|/li|/h[1-6]|h[1-6]|/tr|/pre|pre|/blockquote)\b[^>]*>", re.I)
_DROPPED = re.compile(r"<\s*(script|style)\b.*?<\s*/\s*(?:script|style)\s*>", re.I | re.S)
_TAGS = re.compile(r"<[^>]*>", re.S)
_ENTITY = re.compile(r"&(#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z]{2,8});")
_NAMED = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ", "ndash": "–", "mdash": "—",
    "hellip": "…", "rsquo": "'", "lsquo": "'", "rdquo": '"', "ldquo": '"',
}


def clean(text):
    """Normalised line endings and whitespace, no blank line at either end."""
    return _clean(text).strip("\n")


def one_line(text):
    return " ".join(str(text or "").split())


def _decode(found):
    name = found.group(1)
    try:
        if name[:2] in ("#x", "#X"):
            return chr(int(name[2:], 16))
        if name[0] == "#":
            return chr(int(name[1:]))
    except (ValueError, OverflowError):
        return found.group(0)
    return _NAMED.get(name, found.group(0))


def decode_entities(text):
    """`&amp;` `&#x27;` `&#8217;` ... Unknown names are left as written."""
    return _ENTITY.sub(_decode, text)


def html_to_text(fragment):
    """The readable text of an HTML fragment (an HN comment, a feed body): block tags become line
    breaks, other tags vanish, entities are decoded."""
    bare = _TAGS.sub("", _BLOCKS.sub("\n", _DROPPED.sub(" ", fragment or "")))
    return clean(decode_entities(bare))


def parse_date(text):
    """A date as feeds and search APIs write it: RFC 2822, RFC 3339, or a bare `2026-11-10`,
    `2026-11-10T12:00:00`, `2026-11-10 12:00:00` (taken as UTC). None for anything else."""
    text = (text or "").strip()
    if not text:
        return None
    found = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?", text)
    if not found:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if parsed is None:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
    year, month, day, hour, minute, second, zone = found.groups()
    try:
        moment = datetime.datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0),
                                   tzinfo=UTC)
    except ValueError:
        return None
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        moment -= sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return moment


def from_timestamp(seconds):
    try:
        return datetime.datetime.fromtimestamp(int(seconds), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def iso(moment):
    """A stored time: `2026-11-10T12:00:00Z`; None stays None."""
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z") if moment else None


def from_iso(text):
    return parse_date(text) if isinstance(text, str) else None


def truncate(text, limit):
    """At most `limit` characters, with an ellipsis when something was cut."""
    return text if len(text) <= limit else text[:max(limit - 1, 0)] + "…"


def cap(text, limit):
    """`text` cut to `limit` characters (no ellipsis: it is the item's text, not a preview)."""
    return text[:limit]


def first_line(text, limit):
    """The first non-empty line without Markdown heading marks, at most `limit` characters."""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return truncate(line, limit)
    return None


def at_word(text, limit):
    """`text` cut at a word to at most `limit` characters: what is shown stays a prefix of the real
    text, so a quote copied from it is still found."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > 0 else cut


def words(text):
    """Lower-case words of `text` (letters and digits)."""
    return [word.lower() for word in re.split(r"[^\w]|_", text) if word]


def mentions_lowered(lowered, term):
    """Does `lowered` (lower case already) name `term` as a word (`go` is not in `google`, while
    `c++` and `.net` work)?"""
    term = term.strip().lower()
    if not term:
        return False
    start = lowered.find(term)
    while start != -1:
        end = start + len(term)
        before = start == 0 or not lowered[start - 1].isalnum()
        after = end == len(lowered) or not lowered[end].isalnum()
        if before and after:
            return True
        start = lowered.find(term, start + 1)
    return False


def mentions(text, term):
    return mentions_lowered(text.lower(), term)


# Words that say how senior a job is, not what it is.
ROLE_NOISE = ("senior", "junior", "staff", "principal", "lead", "head", "the", "and", "for", "intern")


def role_words_of(phrase):
    """The words of a role phrase that say what the job is."""
    return [word for word in words(phrase) if len(word) >= 3 and word not in ROLE_NOISE]


def title_fits_roles(title, roles):
    """Does a text share a meaningful word with one of the role phrases (`engineer` also matches
    `engineers` and `engineering`)? No roles means everything fits."""
    wanted = {word for role in roles for word in role_words_of(role)}
    if not wanted:
        return True
    for word in words(title):
        for target in wanted:
            if word == target or (len(target) >= 5 and len(word) >= 5 and (word.startswith(target) or target.startswith(word))):
                return True
    return False


def neutralize(text, tag):
    """Keep `text` from opening or closing a `<tag>` block: `<tag` and `</tag` get a space after `<`."""
    return re.sub(r"<(?=/?" + re.escape(tag) + ")", "< ", text, flags=re.I)


def normalize_url(raw):
    """The form items are stored and compared under: http(s) with a host, lower-case scheme and
    host, no fragment, `/` for an empty path. None for anything else."""
    import urllib.parse

    try:
        parts = urllib.parse.urlsplit((raw or "").strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host or parts.username or parts.password:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != {"http": 80, "https": 443}[parts.scheme.lower()]:
        netloc += f":{port}"
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def host_of(url):
    import urllib.parse

    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""
