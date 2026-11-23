"""One job, one row: a canonical address and a company+role key, checked against every lead and job."""
import re
import urllib.parse

from . import state

_TRACKING = {
    "gh_src", "lever-source", "lever-origin", "source", "src", "ref", "referrer", "fbclid", "gclid", "trk",
    "trackingid", "refid", "mc_cid", "mc_eid", "mkt_tok", "campaign", "cmpid",
}
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "jsc", "plc", "sa", "ag", "pte",
    "pty", "bv",
}


def canonical_url(raw):
    """The address without what does not identify the job: `www.`, fragment, a trailing `/apply`,
    tracking parameters, parameter order, trailing slash. Empty when it is not http(s)."""
    try:
        parts = urllib.parse.urlsplit((raw or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2 and segments[-1].lower() in ("apply", "application"):
        segments.pop()
    query = sorted(
        (key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING
    )
    netloc = host if port is None else f"{host}:{port}"
    path = "/" + "/".join(segments) if segments else ""
    return urllib.parse.urlunsplit((parts.scheme, netloc, path, urllib.parse.urlencode(query), ""))


def _words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def company_words(company):
    """The company's words without trailing legal words ("Acme Inc." -> acme)."""
    words = _words(company)
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return words


def dedup_key(company, role):
    """`acme-senior-backend-engineer`. Empty (never a match) when either side is empty."""
    company_part = company_words(company or "")
    role_part = _words(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", role or ""))
    if not company_part or not role_part:
        return ""
    return "-".join(company_part) + "-" + "-".join(role_part)


def find_known(url="", company="", role=""):
    """Where this job already is: `{"kind": "lead" | "job", "id": ..., "state"/"status": ...}` or None.

    Leads are `leads.json` (a list; fields `id`, `url` (canonical), `dedup_key`, `state`); jobs are
    `jobs/<slug>/job.json` (`source`, `apply_url`, `company`, `role`)."""
    canonical = canonical_url(url)
    key = dedup_key(company, role)
    for job in state.list_jobs():
        addresses = {canonical_url(job.get("source", "")), canonical_url(job.get("apply_url", ""))} - {""}
        if (canonical and canonical in addresses) or (key and key == dedup_key(job.get("company", ""), job.get("role", ""))):
            return {"kind": "job", "id": job["slug"], "status": job["status"]}
    for lead in state.read_json("leads.json", []):
        if (canonical and lead.get("url") == canonical) or (key and lead.get("dedup_key") == key):
            return {"kind": "lead", "id": lead["id"], "state": lead.get("state", "new")}
    return None
