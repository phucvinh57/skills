"""Leads: the jobs discovery found, before the user decides.

`leads.json` is a list of rows:

    {id, source_id, url (canonical), dedup_key, company, role, location, snippet,
     jd_path ("leads/<id>/jd.md"), score (float | null), score_json,
     state: "new" | "dismissed" | "promoted", dismiss_reason, filtered_reason, job_slug,
     found_at, dismissed_at}

- A lead is not a job: it becomes one only when the user asks (`promote`, idempotent).
- `filtered_reason` hides a `new` lead the preferences now filter out (`refilter`); hiding is not a
  dismissal, and a later change of the preferences shows it again.
- A dismissed lead is never deleted: dedup still knows the posting, so it is not found again.
"""
import os

from . import dedup, prefs as prefs_model, score as scoring, state
from .cli import Fail

FILE = "leads.json"
STATES = ("new", "dismissed", "promoted")
MAX_POSTING_CHARS = 40_000  # longest posting text kept


def load():
    return state.read_json(FILE, [])


def save(rows):
    state.write_json(FILE, rows)


def find(rows, lead_id):
    for row in rows:
        if row.get("id") == lead_id:
            return row
    raise Fail(f"no lead {lead_id}", hint="`jobpilot.py lead list` shows the leads")


def get(lead_id):
    return find(load(), lead_id)


def jd_file(lead_id):
    return os.path.join("leads", str(lead_id), "jd.md")


def posting_text(lead):
    """The saved posting text of a lead; empty when it is gone."""
    return (state.read_text(lead.get("jd_path") or jd_file(lead["id"])) or "").strip()


def candidate_of(lead):
    return {"url": lead.get("url", ""), "title": lead.get("role", ""), "company": lead.get("company", ""),
            "location": lead.get("location", ""), "snippet": ""}


def add(rows, source_id, candidate, company, text, dismiss_reason=None):
    """Append a lead to `rows` (the caller saves them) and write its posting text. `candidate["url"]`
    is canonical already. With `dismiss_reason` the lead is stored dismissed: the hard filters
    rejected it, and it must not be fetched again."""
    lead_id = max([row.get("id", 0) for row in rows] + [0]) + 1
    now = state.now()
    lead = {
        "id": lead_id,
        "source_id": source_id,
        "url": candidate["url"],
        "dedup_key": dedup.dedup_key(company, candidate.get("title", "")),
        "company": company,
        "role": candidate.get("title", ""),
        "location": candidate.get("location", ""),
        "snippet": candidate.get("snippet", ""),
        "jd_path": jd_file(lead_id),
        "score": None,
        "score_json": None,
        "state": "dismissed" if dismiss_reason else "new",
        "dismiss_reason": dismiss_reason,
        "filtered_reason": None,
        "job_slug": None,
        "found_at": now,
        "dismissed_at": now if dismiss_reason else None,
    }
    state.write_text(lead["jd_path"], text.strip()[:MAX_POSTING_CHARS] + "\n")
    rows.append(lead)
    return lead


# --- the queue -----------------------------------------------------------------------------------

def sort_queue(rows):
    """Best score first; unscored leads last; the newest first among equals."""
    newest = sorted(rows, key=lambda lead: (lead.get("found_at") or "", lead.get("id", 0)), reverse=True)
    return sorted(newest, key=lambda lead: (lead.get("score") is None, -(lead.get("score") or 0)))


def listed(state_name="new", unscored=False, show_hidden=False):
    rows = [lead for lead in load() if lead.get("state") == state_name]
    if state_name == "new":
        if not show_hidden:
            rows = [lead for lead in rows if not lead.get("filtered_reason")]
        if unscored:
            rows = [lead for lead in rows if lead.get("score") is None]
        return sort_queue(rows)
    if state_name == "dismissed":
        return sorted(rows, key=lambda lead: (lead.get("dismissed_at") or lead.get("found_at") or "", lead.get("id", 0)),
                      reverse=True)
    return sorted(rows, key=lambda lead: lead.get("id", 0), reverse=True)


def waiting_for_score():
    """The ids of the leads in the queue that nobody rated yet, oldest first."""
    return sorted(lead["id"] for lead in listed("new", unscored=True))


def row_view(lead):
    """One lead as a list shows it: short fields only, never posting text."""
    view = {
        "id": lead["id"], "company": lead.get("company", ""), "role": lead.get("role", ""),
        "location": lead.get("location", ""), "url": lead.get("url", ""), "state": lead.get("state", "new"),
        "score": lead.get("score"),
    }
    if lead.get("score_json"):
        view["why"] = scoring.top_reasons(lead["score_json"], 2)
    for name in ("filtered_reason", "dismiss_reason", "job_slug"):
        if lead.get(name):
            view[name] = lead[name]
    return view


# --- filters again, after the preferences change -------------------------------------------------

def filtered_reason(prefs, lead):
    """Why the saved preferences hide `lead` from the queue, if they do: the hard filters on
    company, role, location and the saved posting text, then the stored score's eligibility answer."""
    return scoring.hard_filter(prefs, candidate_of(lead), posting_text(lead)) or scoring.ineligible_reason(
        prefs, lead.get("score_json"))


def refilter(prefs=None):
    """Hide the waiting leads a source found that the preferences now filter out, and show again
    the hidden ones they let pass. A lead the user added by hand is theirs: never filtered. Returns
    `{"filtered": [ids], "unfiltered": [ids]}`."""
    prefs = prefs or prefs_model.load()
    rows = load()
    changed = {"filtered": [], "unfiltered": []}
    for lead in rows:
        if lead.get("state") != "new" or lead.get("source_id") is None:
            continue
        reason = filtered_reason(prefs, lead)
        if reason == lead.get("filtered_reason"):
            continue
        lead["filtered_reason"] = reason
        changed["filtered" if reason else "unfiltered"].append(lead["id"])
    if changed["filtered"] or changed["unfiltered"]:
        save(rows)
    return changed


# --- score ---------------------------------------------------------------------------------------

def set_score(lead_id, answer):
    """Store the agent's rating of a `new` lead: the total is computed here. When the answer says
    the posting hires only outside `work_from`, the lead is dismissed with that reason."""
    prefs = prefs_model.load()
    rows = load()
    lead = find(rows, lead_id)
    if lead.get("state") != "new":
        raise Fail(f"lead {lead_id} is {lead.get('state')}: only a new lead is rated")
    result = scoring.scored(prefs, answer)
    lead["score"] = result["total"]
    lead["score_json"] = result
    reason = scoring.ineligible_reason(prefs, result)
    if reason:
        lead.update(state="dismissed", dismiss_reason=reason, dismissed_at=state.now(), filtered_reason=None)
    save(rows)
    return lead


# --- dismiss, restore, promote -------------------------------------------------------------------

def dismiss(lead_id, reason=""):
    rows = load()
    lead = find(rows, lead_id)
    if lead.get("state") == "dismissed":
        return lead
    if lead.get("state") == "promoted" and _job_of(lead):
        raise Fail(f"lead {lead_id} is a job already ({lead['job_slug']})", hint="dismiss the job instead")
    lead.update(state="dismissed", dismiss_reason=(reason or "").strip() or None, dismissed_at=state.now())
    save(rows)
    return lead


def restore(lead_id):
    """A dismissed lead is `new` again, its reason forgotten. Returns `(lead, hidden, excluded)`:
    `hidden` is why the saved preferences still keep it out of the queue (a lead from a source),
    `excluded` whether its company is still in `exclude_companies` (Restore leaves it there)."""
    prefs = prefs_model.load()
    rows = load()
    lead = find(rows, lead_id)
    excluded = scoring.excluded_company(prefs, lead.get("company", "")) is not None
    if lead.get("state") == "new":
        return lead, lead.get("filtered_reason"), excluded
    if lead.get("state") != "dismissed":
        raise Fail(f"lead {lead_id} is {lead.get('state')}: only a dismissed lead is restored")
    hidden = filtered_reason(prefs, lead) if lead.get("source_id") is not None else None
    lead.update(state="new", dismiss_reason=None, dismissed_at=None, filtered_reason=hidden)
    save(rows)
    return lead, hidden, excluded


def _job_of(lead):
    slug = lead.get("job_slug")
    return state.read_json(os.path.join("jobs", slug, "job.json")) if slug else None


def _web_link(url):
    """The posting's address when it is a web link (a lead from pasted text has none)."""
    return url if (url or "").lower().startswith(("http://", "https://")) else ""


def _write_job_jd(job, lead, text):
    body = f"# {lead.get('company', '')} — {lead.get('role', '')}\nSource: {lead.get('url', '')}\n\n{text.strip()}\n"
    return state.write_text(os.path.join("jobs", job["slug"], "jd.md"), body)


def strip_jd_header(text):
    """The posting text of a job's `jd.md`, without its `# company - role` and `Source:` lines."""
    rest = text or ""
    if rest.startswith("# "):
        rest = rest.split("\n", 1)[1] if "\n" in rest else ""
        if rest.startswith("Source:"):
            rest = rest.split("\n", 1)[1] if "\n" in rest else ""
    return rest.strip()


def promote(lead_id):
    """Turn a lead into a draft job: the job is created with the posting text as its `jd.md`, the
    lead is marked `promoted`. Idempotent: a promoted lead returns its job;
    if that job is gone, a new one is created. Only the user's wish calls this; discovery never does.
    Returns `(lead, job, created)`."""
    rows = load()
    lead = find(rows, lead_id)
    if lead.get("state") == "dismissed":
        raise Fail("this lead was dismissed", hint=f"restore it first: `jobpilot.py lead restore {lead_id}`")
    if lead.get("state") == "promoted":
        job = _job_of(lead)
        if job:
            # A crash between the two writes leaves a job without its text.
            if not state.read_text(os.path.join("jobs", job["slug"], "jd.md")):
                _write_job_jd(job, lead, posting_text(lead))
            return lead, job, False
    # Refuse before anything is created: without the text there is no CV to write.
    text = posting_text(lead)
    if not text:
        raise Fail("the posting text of this lead was not saved", hint="add the posting again from its link or a file")
    link = _web_link(lead.get("url", ""))
    job = state.new_job(lead.get("company", ""), lead.get("role", ""), source=link, apply_url=link, lead_id=lead["id"])
    _write_job_jd(job, lead, text)
    # The user chose it: a filter that hid it meanwhile no longer applies.
    lead.update(state="promoted", job_slug=job["slug"], filtered_reason=None)
    save(rows)
    return lead, job, True
