"""Matching a reply email to a job, and recording what it says.

The matching is deterministic (sender domain, company name); the host agent only classifies the
email among the candidates this module returns (`references/track.md`), and the result is checked
here again before anything is recorded. A status only moves forward.
"""
import ipaddress
import math
import re
from urllib.parse import urlsplit

from . import state
from .cli import Fail

# Mailers and job boards: a sender here says nothing about WHICH company wrote.
ATS_DOMAINS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com",
    "workday.com", "linkedin.com", "smartrecruiters.com", "workable.com", "workablemail.com", "icims.com",
    "jobvite.com", "bamboohr.com", "recruitee.com", "teamtailor.com", "breezy.hr", "applytojob.com", "jazzhr.com",
    "taleo.net", "successfactors.com", "successfactors.eu", "personio.de", "personio.com", "comeet.co", "comeet.com",
    "eightfold.ai", "pinpointhq.com", "rippling.com", "oraclecloud.com", "dayforcehcm.com", "indeed.com",
    "glassdoor.com", "wellfound.com", "angel.co", "ziprecruiter.com", "topcv.vn", "itviec.com", "vietnamworks.com",
)

# Second-level suffixes where the registrable domain has three labels (`acme.co.uk`).
SECOND_LEVEL = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.nz", "co.jp", "co.kr", "co.in", "co.za", "com.br",
    "com.cn", "com.hk", "com.mx", "com.sg", "com.tr", "com.tw", "com.vn", "edu.vn", "net.vn", "org.vn",
)

# Words after which a company name is "legal form" noise (`Acme Inc.`).
LEGAL_SUFFIXES = ("inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh", "jsc", "plc", "sa",
                  "ag", "pte", "pty", "bv")

# Subject/snippet hints that an email may concern a job application (used only when no
# deterministic match exists, so the whole inbox is never classified).
JOB_HINTS = (
    "application", "applying", "applied", "interview", "candidate", "candidacy", "position", "recruit", "hiring",
    "your resume", "your cv", "your interest", "next steps", "unfortunately", "job offer", "offer letter",
    "assessment", "screening", "ứng tuyển", "phỏng vấn", "tuyển dụng",
)

# Most jobs offered to the agent when only the keyword gate matched.
MAX_FALLBACK_CANDIDATES = 40

# A classification below this is never applied on its own: the user is asked.
AUTO_APPLY_CONFIDENCE = 0.8

# The jobs an email can belong to. The app used `submitting` or later (it submitted the form
# itself). Here the user submits by hand and may not have said so yet, so a job whose CV is ready
# already counts. Drafts, failed and dismissed jobs get no mail.
POOL_FROM_STATUS = "cv_ready"

KINDS = ("confirmation", "rejection", "interview", "offer", "other")
# The status each kind of email moves a job to (`other` moves nothing).
TARGET_STATUS = {"confirmation": "applied", "interview": "interview", "rejection": "rejected", "offer": "offer"}

EMAILS = "emails.json"
_RANK = {status: rank for rank, status in enumerate(("cv_ready", "submitting", "applied", "interview", "rejected", "offer"), 1)}


# --- domains -------------------------------------------------------------------------------------

def _host(text):
    return (text or "").strip().rstrip(".").lower()


def registrable_domain(host):
    """`careers.acme.co.uk` -> `acme.co.uk`."""
    host = _host(host)
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    keep = 3 if ".".join(labels[-2:]) in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def is_ats_host(host):
    """Is `host` (or a parent of it) an ATS / job-board domain?"""
    host = _host(host)
    return any(host == domain or host.endswith("." + domain) for domain in ATS_DOMAINS)


def _is_ip(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def url_company_domain(raw):
    """The company domain of one web address; None when it is not a URL, or its host is
    `localhost`, an IP address or an ATS / job board."""
    try:
        parts = urlsplit((raw or "").strip())
        host = parts.hostname
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "localhost" or _is_ip(host) or is_ats_host(host):
        return None
    return registrable_domain(host)


def company_domain(job):
    """The job's own company domain, from `apply_url` then `source`."""
    for raw in (job.get("apply_url"), job.get("source")):
        domain = url_company_domain(raw) if raw else None
        if domain:
            return domain
    return None


def parse_from(sender):
    """Split a `From` header into `(display name, address)`."""
    sender = (sender or "").strip()
    left, right = sender.rfind("<"), sender.rfind(">")
    if left != -1 and right != -1 and left < right:
        return sender[:left].strip().strip('"').strip(), sender[left + 1:right].strip()
    return "", sender


def sender_host(sender):
    """Lower-cased host part of a `From` header's address."""
    address = parse_from(sender)[1]
    if "@" not in address:
        return None
    host = address.rsplit("@", 1)[1].strip().rstrip(">").lower()
    return host or None


def _words(text):
    return [word.lower() for word in re.findall(r"[^\W_]+", text or "")]


def _company_words(company):
    """The company's words without trailing legal-form suffixes."""
    words = _words(company)
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return words


def mentions_company(company, text):
    """Does the company name appear as whole words in `text`?"""
    needle = _company_words(company)
    if len("".join(needle)) < 2:
        return False
    hay = _words(text)
    size = len(needle)
    return any(hay[start:start + size] == needle for start in range(len(hay) - size + 1))


def _domain_stem_is_company(sender_registrable, company):
    """Does the sender's domain label equal the company name (`acme.com` vs "Acme Inc.")?"""
    stem = sender_registrable.split(".")[0].replace("-", "")
    name = "".join(_company_words(company))
    return len(name) >= 3 and stem == name


# --- candidates ----------------------------------------------------------------------------------

def deterministic_candidates(sender, subject, pool):
    """Jobs the email plausibly belongs to: `[(job, "company_domain" | "ats_mailer")]`.

    `company_domain`: the sender's domain is the job's company domain, or is named like the company.
    `ats_mailer`: the sender is an ATS mailer and the company name is in the subject or sender name."""
    host = sender_host(sender)
    if not host:
        return []
    registrable = registrable_domain(host)
    ats = is_ats_host(host)
    text = f"{subject} {parse_from(sender)[0]}"
    found = []
    for job in pool:
        company = job.get("company", "")
        if ats:
            if mentions_company(company, text):
                found.append((job, "ats_mailer"))
        elif company_domain(job) == registrable or _domain_stem_is_company(registrable, company):
            found.append((job, "company_domain"))
    return found


def looks_job_related(subject, snippet=""):
    """Cheap keyword gate before an unmatched email is offered for classification."""
    text = f"{subject} {snippet}".lower()
    return any(hint in text for hint in JOB_HINTS)


def in_pool(status):
    return _RANK.get(status, 0) >= _RANK[POOL_FROM_STATUS]


def pool():
    """The jobs an email can belong to (see `POOL_FROM_STATUS`)."""
    return [job for job in state.list_jobs() if in_pool(job.get("status"))]


def full_from(address, name=""):
    """`Name <address>` from the parts the agent has; a header given whole is kept."""
    address = (address or "").strip()
    name = " ".join((name or "").split())
    if name and "<" not in address:
        return f"{name} <{address}>"
    return address


def candidates(sender, subject, snippet="", jobs=None):
    """`(candidates, how)`: `how` is `matched` (deterministic rules), `job_hints` (nothing matched
    but the text reads like a job email: up to 40 pool jobs) or `none` (not about a job)."""
    jobs = pool() if jobs is None else jobs
    found = deterministic_candidates(sender, subject, jobs)
    if found:
        return found, "matched"
    if looks_job_related(subject, snippet):
        return [(job, "job_hints") for job in jobs[:MAX_FALLBACK_CANDIDATES]], "job_hints"
    return [], "none"


# --- classification ------------------------------------------------------------------------------

def one_line(text):
    return " ".join(str(text or "").split())


def parse_classification(raw):
    """The agent's classification in a checked form: an unknown kind is `other`, the confidence is
    kept within 0..1 (not a number = 0), the summary is one line of at most 200 characters."""
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
        confidence = 0.0
    confidence = min(1.0, max(0.0, float(confidence)))
    job = raw.get("job")
    return {"job": job if isinstance(job, str) and job else None, "kind": kind, "confidence": confidence,
            "summary": one_line(raw.get("summary"))[:200]}


def target_status(kind):
    return TARGET_STATUS.get(kind)


def emails():
    stored = state.read_json(EMAILS, [])
    return stored if isinstance(stored, list) else []


def find_email(email_id):
    return next((email for email in emails() if email.get("id") == email_id), None)


def store_email(email):
    stored = [row for row in emails() if row.get("id") != email["id"]]
    stored.append(email)
    state.write_json(EMAILS, stored)


def needs_review(email):
    """A classified email below the auto-apply confidence that the user has not resolved yet."""
    return bool(email.get("job")) and not email.get("applied") and not email.get("resolved") \
        and email.get("confidence", 0) < AUTO_APPLY_CONFIDENCE and email.get("kind") in TARGET_STATUS


def review_message(email):
    percent = int(round(email["confidence"] * 100))
    return f"needs review: {email['subject']} — looks like {email['kind']} ({percent}%): {email['summary']}"


def record_outcome(email, slug, kind):
    """Move the job forward if the rule allows and add the `email` timeline event. Returns the
    status change `{from, to}`, or None. Never moves a status back: the timeline says
    "(status stays X)" instead."""
    job = state.load_job(slug)
    summary = email.get("summary") or ""
    text = f"email: {email.get('subject', '')} — {summary}"
    change = None
    target = target_status(kind)
    current = job["status"]
    if target and current != target:
        if state.can_advance_to(current, target):
            state.set_status(slug, target, summary)
            change = {"from": current, "to": target}
        else:
            text += f" (status stays {state.LABELS[current]})"
    state.add_event(slug, "email", text)
    return change


def report(raw, confirmed=False):
    """Record one classified email (`track email`). Returns what happened."""
    email_id = one_line(raw.get("id"))
    if not email_id:
        raise Fail("the classification has no `id`", hint="use the message id of the mail tool, or any stable id for a pasted email")
    known = find_email(email_id)
    if known:
        if confirmed and needs_review(known):
            # The user agrees with a "needs review" email: apply it like a confident match.
            change = record_outcome(known, known["job"], known["kind"])
            known.update(confidence=1.0, applied=change is not None, resolved=True)
            store_email(known)
            return {"recorded": True, "email": known, "status_change": change, "needs_confirmation": False}
        return {"recorded": False, "already_recorded": True, "email": known, "needs_confirmation": needs_review(known)}

    sender = full_from(one_line(raw.get("from")), raw.get("name") or "")
    subject = one_line(raw.get("subject"))
    parsed = parse_classification(raw)
    found, how = candidates(sender, subject, one_line(raw.get("snippet")))
    allowed = [job["slug"] for job, _ in found]
    if parsed["job"] and parsed["job"] not in allowed:
        raise Fail(
            f"{parsed['job']!r} is not a candidate for this sender and subject",
            hint="`job` must be one of the slugs `track email-candidates` returned for the same --from/--subject"
                 " (and --name/--snippet), or null. Nothing was recorded.",
            candidates=allowed,
        )
    # A generic "other" mail is only linked when the classification is sure.
    sure = parsed["confidence"] >= AUTO_APPLY_CONFIDENCE or confirmed
    linked = parsed["job"] if parsed["kind"] != "other" or sure else None
    email = {
        "id": email_id, "job": linked, "from": sender, "subject": subject,
        "received_at": one_line(raw.get("received_at")), "kind": parsed["kind"],
        "confidence": 1.0 if confirmed and linked else parsed["confidence"], "summary": parsed["summary"],
        "applied": False,
    }
    change, ask = None, False
    if linked:
        if sure:
            change = record_outcome(email, linked, parsed["kind"])
            email["applied"] = change is not None
        else:
            state.add_event(linked, "email_review", review_message(email))
            ask = True
    store_email(email)
    out = {"recorded": True, "email": email, "status_change": change, "needs_confirmation": ask, "matched_by": how}
    if ask:
        out["next"] = ("Ask the user whether this reading is right. Yes: run the same command again with --confirmed."
                       " No: `track email-dismiss --id ID`.")
    return out


def dismiss(email_id):
    """The user says the suggestion is wrong: the email stays linked but counts as `other`."""
    email = find_email(email_id)
    if not email:
        raise Fail(f"no recorded email {email_id!r}")
    email.update(kind="other", applied=False, resolved=True)
    store_email(email)
    if email.get("job"):
        state.add_event(email["job"], "email_review", f"review dismissed: {email.get('subject', '')}")
    return email
