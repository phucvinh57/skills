"""Scoring a posting against the preferences: hard filters in code first, then the agent rates
five criteria, then the weighted total in code.

- `hard_filter`: an excluded company, a location or remote mismatch, a place outside the countries
  the user may work from (`work_from`, see `region`), a seniority far off the wanted levels. A
  filtered posting is stored as dismissed with the reason, so it is not fetched again.
- `parse_answer`: the agent's answer: skills, domain, location, salary, seniority, each 0 to 100
  with a one-line reason, and whether the posting hires where the user may work (`eligible`).
- `weighted`: `sum(weight * score) / sum(weight)` over the criteria that could be judged (a
  posting without a salary says nothing about the salary criterion, so it does not count as 0).
  The agent never supplies the total.
"""
import math
import re

from . import region
from .cli import Fail
from .prefs import CRITERIA

SCORE_JD_CHARS = 12_000  # longest posting text shown for scoring
PROFILE_CHARS = 6_000  # longest compact profile shown for scoring
REASON_CHARS = 160  # longest reason kept per criterion
ELIGIBLE = ("yes", "no", "unclear")


def words(text):
    """Lower-case runs of letters and digits."""
    return re.findall(r"[^\W_]+", (text or "").lower())


def one_line(text, limit):
    line = " ".join((text or "").split())
    return line[:limit].rstrip() + "..." if len(line) > limit else line


def _mentions(text, term):
    from . import sources

    return sources.mentions(text, term)


# --- hard filters --------------------------------------------------------------------------------

# Legal-form words that make no difference to whether two names are the same company.
LEGAL_WORDS = ("inc", "llc", "ltd", "limited", "corp", "corporation", "co", "gmbh", "plc", "pte", "pty")


def _company_words(name):
    found = words(name)
    while len(found) > 1 and found[-1] in LEGAL_WORDS:
        found.pop()
    return found


def excluded_company(prefs, company):
    """The company's name contains an excluded name as whole words (`Meta` excludes `Meta
    Platforms` but not `Metabase`)."""
    have = _company_words(company)
    if not have:
        return None
    for excluded in prefs.get("exclude_companies", []):
        need = _company_words(excluded)
        if need and any(have[i:i + len(need)] == need for i in range(len(have) - len(need) + 1)):
            return f"excluded company: {one_line(excluded, 60)}"
    return None


def _is_remote(text):
    return any(_mentions(text, key) for key in ("remote", "work from home", "wfh"))


def _location_mismatch(prefs, candidate, jd):
    """Only a clear mismatch counts: an unknown location never does, and a posting that says
    "remote" anywhere is not "on-site"."""
    location = (candidate.get("location") or "").strip()
    if not location:
        return None
    if _is_remote(location) or _is_remote(candidate.get("title") or "") or _is_remote(jd):
        return None
    if prefs.get("remote") == "remote":
        return f"location: on-site in {one_line(location, 60)}, you want remote"
    wanted = prefs.get("locations") or []
    if wanted and not any(_mentions(location, place) or _mentions(place, location) for place in wanted):
        return f"location: {one_line(location, 60)} is outside {one_line(', '.join(wanted), 80)}"
    return None


def work_from(prefs):
    return [entry.strip() for entry in prefs.get("work_from") or [] if entry.strip()]


def _outside_reason(places, allowed):
    names = ", ".join(region.place_name(place) for place in places)
    return f"location: hires in {one_line(names, 80)}, you can work from {one_line(', '.join(allowed), 80)}"


def _work_from_mismatch(prefs, candidate, jd):
    """The posting hires only outside the countries the user may work from, remote or not. The
    location decides when it names a place the gazetteer knows ("Remote - US"), unless it leaves the
    place out or names a time zone; else the posting's clear limit phrases do. A plain "Remote", an
    empty or unknown location and a posting without a limit phrase pass; so does every posting when
    `work_from` is empty. The reason names places only, never the posting's text."""
    allowed = work_from(prefs)
    if not allowed:
        return None
    location = (candidate.get("location") or "").strip()
    named = region.places_in(location)
    if named and not region.location_is_not_a_limit(location):
        ok = any(region.covers(place, allowed) for place in named) or any(_mentions(location, entry) for entry in allowed)
        return None if ok else _outside_reason(named, allowed)
    limits = region.jd_limits(jd)
    limited = limits.places()
    if not limited or any(region.covers(place, allowed) for place in limited) or limits.mentions(allowed):
        return None
    return _outside_reason(limited, allowed)


LEVELS = ("intern", "junior", "mid-level", "senior", "lead or staff", "principal", "director")
_LEVEL_WORDS = {
    0: ("intern", "internship", "trainee", "apprentice"),
    1: ("junior", "jr", "entry", "graduate", "fresher"),
    2: ("mid", "middle", "intermediate"),
    3: ("senior", "sr"),
    4: ("lead", "staff", "manager"),
    5: ("principal", "architect", "distinguished"),
    6: ("director", "vp", "vice", "head", "chief", "cto"),
}
_LEVEL_OF = {word: level for level, names in _LEVEL_WORDS.items() for word in names}


def level_of(text):
    """The level a job title or a wanted level says: the highest keyword in it."""
    found = [_LEVEL_OF[word] for word in words(text) if word in _LEVEL_OF]
    return max(found) if found else None


def _seniority_far_off(prefs, title):
    """The title's level is two or more steps away from every wanted level. Titles without a
    level word pass, and so do preferences without one."""
    wanted = [level for level in (level_of(entry) for entry in prefs.get("seniority") or []) if level is not None]
    job = level_of(title)
    if job is None or not wanted or any(abs(job - level) < 2 for level in wanted):
        return None
    labels = []
    for level in wanted:
        if not labels or labels[-1] != LEVELS[level]:
            labels.append(LEVELS[level])
    return f"seniority: the title says {LEVELS[job]}, you look for {' or '.join(labels)}"


def hard_filter(prefs, candidate, jd):
    """Why a posting is dropped before anyone rates it, or None when it passes. `candidate` has
    `title`, `company`, `location`. The reason names the preference, never the posting's text."""
    jd = jd or ""
    return (
        excluded_company(prefs, candidate.get("company") or "")
        or _location_mismatch(prefs, candidate, jd)
        or _work_from_mismatch(prefs, candidate, jd)
        or _seniority_far_off(prefs, candidate.get("title") or "")
    )


# --- scores --------------------------------------------------------------------------------------

def weighted(criteria, weights):
    """The weighted sum, 0 to 100 with one decimal; None when no criterion with a weight could be
    judged."""
    total = amount = 0
    for name in CRITERIA:
        weight = int(weights.get(name, 0))
        criterion = criteria.get(name) or {}
        if weight > 0 and criterion.get("known"):
            amount += weight * min(int(criterion.get("score", 0)), 100)
            total += weight
    if total == 0:
        return None
    return math.floor(amount / total * 10 + 0.5) / 10


def parse_answer(answer):
    """The agent's answer, checked: `(criteria, eligible, eligible_reason)`. A wrong shape is a
    `Fail` that says what to fix; a `total` the agent added is ignored."""
    if not isinstance(answer, dict):
        raise Fail("the score must be a JSON object", hint="see references/discover.md for the shape")
    criteria = {}
    for name in CRITERIA:
        item = answer.get(name)
        if not isinstance(item, dict):
            raise Fail(f"`{name}` is missing", hint='each criterion is {"score": 0-100, "known": true|false, "reason": "..."}')
        value, known, reason = item.get("score"), item.get("known"), item.get("reason")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise Fail(f"`{name}.score` must be a whole number from 0 to 100")
        if not isinstance(known, bool):
            raise Fail(f"`{name}.known` must be true or false")
        if not isinstance(reason, str):
            raise Fail(f"`{name}.reason` must be one line of text")
        criteria[name] = {"score": value, "known": known, "reason": one_line(reason, REASON_CHARS)}
    eligible = answer.get("eligible")
    if eligible not in ELIGIBLE:
        raise Fail("`eligible` must be one of: " + ", ".join(ELIGIBLE))
    reason = answer.get("eligible_reason")
    if not isinstance(reason, str):
        raise Fail("`eligible_reason` must be one line of text (it may be empty)")
    return criteria, eligible, one_line(reason, REASON_CHARS)


def scored(prefs, answer):
    """What is stored for a rated lead: the total computed here, the criteria, the weights used,
    the eligibility answer and the `work_from` it was judged against."""
    criteria, eligible, eligible_reason = parse_answer(answer)
    weights = dict(prefs["weights"])
    return {
        "total": weighted(criteria, weights),
        "criteria": criteria,
        "weights": weights,
        "eligible": eligible,
        "eligible_reason": eligible_reason,
        "judged_work_from": work_from(prefs),
    }


def _judged_against(allowed, judged):
    """Every entry of `work_from` was in the (non-empty) `judged` list, ignoring case."""
    judged = [entry.strip().lower() for entry in judged if entry.strip()]
    return bool(judged) and all(entry.lower() in judged for entry in allowed)


def ineligible_reason(prefs, score):
    """Why a rated posting is dismissed after all: the answer said the posting hires only outside
    `work_from`, and that answer still holds: `work_from` is set now and was set when the posting
    was rated, and every country in it now was in it then (adding a country voids the answer). The
    reason names places, never the agent's or the posting's text."""
    allowed = work_from(prefs)
    if not score or score.get("eligible") != "no" or not allowed:
        return None
    if not _judged_against(allowed, score.get("judged_work_from") or []):
        return None
    elsewhere = [place for place in region.places_in(score.get("eligible_reason") or "")
                 if not region.covers(place, allowed)]
    if not elsewhere:
        return f"location: the posting hires outside {one_line(', '.join(allowed), 80)}"
    return _outside_reason(elsewhere, allowed)


def top_reasons(score, count):
    """The criteria that were judged and say why, best contribution (`weight * score`) first."""
    rows = []
    for name in CRITERIA:
        criterion = (score.get("criteria") or {}).get(name) or {}
        if criterion.get("known") and (criterion.get("reason") or "").strip():
            weight = int((score.get("weights") or {}).get(name, 0))
            rows.append((weight * criterion["score"], criterion["score"], name, criterion["reason"]))
    rows.sort(key=lambda row: (-row[0], -row[1]))
    return [f"{name} {value}: {reason}" for _, value, name, reason in rows[:count]]


# --- profile -------------------------------------------------------------------------------------

def compact_profile(cv):
    """What is needed to judge a fit, from the profile: headline, summary, skills, roles and
    degrees. No name, contact details or bullets. Hidden entries are left out."""
    lines = []

    def line(label, text):
        if (text or "").strip():
            lines.append(f"{label}: {one_line(text, 600)}\n")

    basics = cv.get("basics") or {}
    line("Headline", basics.get("headline") or "")
    line("Summary", " ".join(str(part) for part in cv.get("summary") or []))
    line("Location", basics.get("location") or "")
    for group in cv.get("skills") or []:
        if not group.get("hidden"):
            line(f"Skills, {one_line(group.get('category') or '', 40)}", ", ".join(str(item) for item in group.get("items") or []))
    for job in cv.get("experience") or []:
        if not job.get("hidden"):
            line("Role", f"{job.get('position', '')} at {job.get('company', '')} ({job.get('start', '')} - {job.get('end', '')})")
    for school in cv.get("education") or []:
        if not school.get("hidden"):
            line("Education", f"{school.get('degree', '')}, {school.get('institution', '')}")
    return "".join(lines)[:PROFILE_CHARS]
