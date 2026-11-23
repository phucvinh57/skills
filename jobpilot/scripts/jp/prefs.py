"""The saved job-search preferences: what the user wants, what to avoid, and how much each
criterion counts. Every field is optional, so `default()` is valid.

    preferences.json        the current document
    preferences.undo.json   the document before the last change (one step of undo)

A change is a `patch`: only the fields that change. A list replaces the saved list, `null` clears
an optional value, `weights` merge per criterion and are rescaled to 100 in total.
"""
import os

from . import state
from .cli import Fail

FILE = "preferences.json"
UNDO_FILE = "preferences.undo.json"

REMOTE = ("any", "remote", "hybrid", "onsite")
REMOTE_LABELS = {"any": "Any", "remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}
CRITERIA = ("skills", "domain", "location", "salary", "seniority")
DEFAULT_WEIGHTS = {"skills": 35, "domain": 20, "location": 15, "salary": 15, "seniority": 15}
LIST_FIELDS = (
    "roles", "seniority", "must_skills", "nice_skills", "avoid_skills", "domains", "avoid_domains", "locations",
    "work_from", "company_size", "company_stage", "languages", "exclude_companies",
)
FIELDS = LIST_FIELDS + ("remote", "salary_min", "currency", "visa_needed", "notes", "weights")
# The fields the hard filters and the eligibility rule read.
FILTER_FIELDS = ("exclude_companies", "remote", "locations", "work_from", "seniority")

MAX_LIST_ITEMS = 30
MAX_ITEM_CHARS = 80
MAX_NOTES_CHARS = 2000
MAX_SALARY = 1_000_000_000  # a typo guard, in the user's currency


def default():
    prefs = {name: [] for name in LIST_FIELDS}
    prefs.update(remote="any", salary_min=None, currency=None, visa_needed=None, notes="", weights=dict(DEFAULT_WEIGHTS))
    return {name: prefs[name] for name in _ORDER}


_ORDER = (
    "roles", "seniority", "must_skills", "nice_skills", "avoid_skills", "domains", "avoid_domains", "locations",
    "work_from", "remote", "salary_min", "currency", "company_size", "company_stage", "visa_needed", "languages",
    "exclude_companies", "notes", "weights",
)


def normalize_weights(weights):
    """Scaled so the weights sum to exactly 100, keeping their proportions (largest remainder
    rounding). All zeros fall back to the default."""
    values = [int(weights.get(name, 0)) for name in CRITERIA]
    total = sum(values)
    if total == 0:
        return dict(DEFAULT_WEIGHTS)
    out = [value * 100 // total for value in values]
    missing = 100 - sum(out)
    by_remainder = sorted(range(len(CRITERIA)), key=lambda index: -(values[index] * 100 % total))
    for index in by_remainder[:missing]:
        out[index] += 1
    return dict(zip(CRITERIA, out))


def _tidy(items):
    seen, out = set(), []
    for item in items:
        item = str(item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def normalized(prefs):
    """A tidy copy with every field: weights summing to 100, list entries trimmed, empty ones
    dropped and duplicates (ignoring case) removed, blank currency / notes cleared. A criterion
    missing from a partial `weights` object counts as 0; no `weights` at all is the default."""
    out = default()
    for name in LIST_FIELDS:
        value = prefs.get(name)
        out[name] = _tidy(value) if isinstance(value, list) else []
    remote = prefs.get("remote", "any")
    out["remote"] = remote if remote in REMOTE else "any"
    salary = prefs.get("salary_min")
    out["salary_min"] = salary if isinstance(salary, int) and not isinstance(salary, bool) and salary > 0 else None
    currency = (prefs.get("currency") or "").strip().upper()
    out["currency"] = currency or None
    visa = prefs.get("visa_needed")
    out["visa_needed"] = visa if isinstance(visa, bool) else None
    out["notes"] = str(prefs.get("notes") or "").strip()
    weights = prefs.get("weights")
    out["weights"] = normalize_weights(weights) if isinstance(weights, dict) else dict(DEFAULT_WEIGHTS)
    return out


def exists():
    return state.read_json(FILE) is not None


def load():
    """The saved preferences (the default document when nothing was saved yet)."""
    return normalized(state.read_json(FILE) or {})


def require():
    """The saved preferences; refuses when the user was never asked what they want."""
    if not exists():
        raise Fail("there are no preferences yet", hint="ask the user what they look for first (references/preferences.md)",
                   no_preferences=True)
    return load()


# --- patch ---------------------------------------------------------------------------------------

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_list(name, value):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Fail(f"`{name}` must be a list of strings", hint="send the whole list: it replaces the saved one")
    return _tidy(item.strip()[:MAX_ITEM_CHARS] for item in value)[:MAX_LIST_ITEMS]


def merge_patch(current, patch):
    """`current` with `patch` applied, tidied. Anything out of range or of the wrong type is a
    `Fail`, so a bad patch is never half applied."""
    if not isinstance(patch, dict):
        raise Fail("the patch must be a JSON object with the fields that change")
    unknown = [name for name in patch if name not in FIELDS]
    if unknown:
        raise Fail("not a preference: " + ", ".join(sorted(unknown)), hint="the fields are: " + ", ".join(_ORDER))
    out = normalized(current)
    for name in LIST_FIELDS:
        if name in patch:
            out[name] = _clean_list(name, patch[name])
    if "remote" in patch:
        if patch["remote"] not in REMOTE:
            raise Fail("`remote` must be one of: " + ", ".join(REMOTE))
        out["remote"] = patch["remote"]
    if "salary_min" in patch:
        salary = patch["salary_min"]
        if salary is not None and (not _is_int(salary) or salary < 0):
            raise Fail("`salary_min` must be a whole number per year, or null to clear it")
        if salary is not None and salary > MAX_SALARY:
            raise Fail("`salary_min` is not a plausible salary")
        out["salary_min"] = salary or None
    if "currency" in patch:
        currency = patch["currency"]
        if currency is not None and not isinstance(currency, str):
            raise Fail("`currency` must be a 3-letter code such as USD, or null to clear it")
        currency = (currency or "").strip()
        if currency and not (len(currency) == 3 and currency.isascii() and currency.isalpha()):
            raise Fail("`currency` must be a 3-letter code such as USD")
        out["currency"] = currency.upper() or None
    if "visa_needed" in patch:
        if patch["visa_needed"] is not None and not isinstance(patch["visa_needed"], bool):
            raise Fail("`visa_needed` must be true, false or null")
        out["visa_needed"] = patch["visa_needed"]
    if "notes" in patch:
        if not isinstance(patch["notes"], str):
            raise Fail("`notes` must be text")
        out["notes"] = patch["notes"].strip()[:MAX_NOTES_CHARS]
    if "weights" in patch:
        weights = patch["weights"]
        if not isinstance(weights, dict) or any(name not in CRITERIA for name in weights):
            raise Fail("`weights` must be an object with some of: " + ", ".join(CRITERIA))
        merged = dict(out["weights"])
        for name, value in weights.items():
            if not _is_int(value) or not 0 <= value <= 100:
                raise Fail(f"the weight of `{name}` must be a whole number from 0 to 100")
            merged[name] = value
        out["weights"] = normalize_weights(merged)
    return normalized(out)


# --- save and undo -------------------------------------------------------------------------------

def filters_changed(old, new):
    return any(old.get(name) != new.get(name) for name in FILTER_FIELDS)


def _refilter(old, new):
    if not filters_changed(old, new):
        return {"filtered": [], "unfiltered": []}
    from . import leads

    return leads.refilter(new)


def update(new):
    """Save `new` (tidied) and keep the document it replaces for `undo`. Saving what is already
    saved changes nothing (and keeps the undo point). Returns `(saved, leads changed)`: a change to
    a field the filters read filters the waiting leads again."""
    new = normalized(new)
    if exists():
        old = load()
        if old == new:
            return old, {"filtered": [], "unfiltered": []}
    else:
        # Nothing was saved yet: undoing the first change goes back to the default document.
        old = default()
    state.write_json(UNDO_FILE, old)
    state.write_json(FILE, new)
    return new, _refilter(old, new)


def can_undo():
    return state.read_json(UNDO_FILE) is not None


def undo():
    """Put back the preferences from before the last change: `(restored, leads changed)`, or None
    when there is nothing to undo. One level only."""
    previous = state.read_json(UNDO_FILE)
    if previous is None:
        return None
    previous = normalized(previous)
    current = load()
    state.write_json(FILE, previous)
    os.remove(state.path(UNDO_FILE))
    return previous, _refilter(current, previous)


# --- summary -------------------------------------------------------------------------------------

def summary(prefs):
    """A few plain lines the agent can show the user; only what is set."""
    lines = []

    def add(label, value):
        if value:
            lines.append(f"{label}: {value}")

    add("Roles", ", ".join(prefs["roles"]))
    add("Seniority", ", ".join(prefs["seniority"]))
    add("Must-have skills", ", ".join(prefs["must_skills"]))
    add("Nice-to-have skills", ", ".join(prefs["nice_skills"]))
    add("Skills to avoid", ", ".join(prefs["avoid_skills"]))
    add("Domains", ", ".join(prefs["domains"]))
    add("Domains to avoid", ", ".join(prefs["avoid_domains"]))
    add("Locations", ", ".join(prefs["locations"]))
    add("Can work from", ", ".join(prefs["work_from"]))
    if prefs["remote"] != "any":
        add("Remote", REMOTE_LABELS[prefs["remote"]])
    if prefs["salary_min"]:
        amount = f"{prefs['salary_min']:,}"
        add("Minimum salary", f"{amount} {prefs['currency']} per year" if prefs["currency"] else f"{amount} per year")
    add("Company size", ", ".join(prefs["company_size"]))
    add("Company stage", ", ".join(prefs["company_stage"]))
    if prefs["visa_needed"] is not None:
        add("Visa sponsorship", "needed" if prefs["visa_needed"] else "not needed")
    add("Working languages", ", ".join(prefs["languages"]))
    add("Never show", ", ".join(prefs["exclude_companies"]))
    add("Notes", prefs["notes"])
    weights = prefs["weights"]
    if weights != DEFAULT_WEIGHTS:
        add("What counts", ", ".join(f"{name} {weights[name]}" for name in CRITERIA if weights[name]))
    return lines
