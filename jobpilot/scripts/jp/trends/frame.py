"""The market frame: what decides which part of the market a report looks at. It comes from the
user's background (the profile: positions, headline, skills) and place (the preferred locations
and the work mode), never from their wishes (wanted or avoided skills, wanted domains, target
roles). Wishes are checked against the market at the end, so they cannot shape what is counted."""
from .. import state
from ..cli import Fail
from . import text as tx

MAX_ROLES = 3
MAX_ROLE_CHARS = 60
PROFILE_CHARS = 6_000
REMOTE_WORDS = ("remote", "work from home", "wfh")
# Phrases that say a posting does not offer remote work, though they name the word.
NOT_REMOTE = ("no remote", "not remote", "non-remote", "non remote", "remote: no", "remote no", "no wfh", "onsite only",
              "on-site only", "in-office only")
_ONSITE = ("onsite", "on-site", "on site", "office", "in-office", "in office")


def _strings(value):
    if isinstance(value, str):
        value = [value]
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _visible(rows):
    return [row for row in rows if isinstance(row, dict) and not row.get("hidden")] if isinstance(rows, list) else []


def _line(text, limit):
    return tx.one_line(text)[:limit]


def compact_profile(cv):
    """The profile as a few labelled lines, without contact details."""
    out = []

    def line(label, text):
        if text and text.strip():
            out.append(f"{label}: {_line(text, 600)}")

    basics = cv.get("basics") if isinstance(cv.get("basics"), dict) else {}
    line("Headline", str(basics.get("headline") or ""))
    line("Summary", " ".join(_strings(cv.get("summary"))))
    line("Location", str(basics.get("location") or ""))
    for group in _visible(cv.get("skills")):
        line(f"Skills, {_line(str(group.get('category') or ''), 40)}", ", ".join(_strings(group.get("items"))))
    for job in _visible(cv.get("experience")):
        line("Role", f"{job.get('position') or ''} at {job.get('company') or ''} ({job.get('start') or ''} - {job.get('end') or ''})")
    for school in _visible(cv.get("education")):
        line("Education", f"{school.get('degree') or ''}, {school.get('institution') or ''}")
    return ("\n".join(out) + "\n")[:PROFILE_CHARS] if out else ""


def remote_allowed(prefs):
    """The work mode allows remote work: anything but on-site only."""
    mode = prefs.get("work_from", prefs.get("remote"))
    if isinstance(mode, bool):
        return mode
    modes = [text.strip().lower() for text in _strings(mode) if text.strip()]
    return not modes or any(text not in _ONSITE for text in modes)


class MarketFrame:
    """The part of the market a report is about. Reads only `locations` and the work mode of the
    preferences."""

    def __init__(self, cv=None, prefs=None):
        cv = cv if isinstance(cv, dict) else None
        prefs = prefs if isinstance(prefs, dict) else {}
        self.roles = []
        seen = set()
        if cv:
            basics = cv.get("basics") if isinstance(cv.get("basics"), dict) else {}
            phrases = [str(job.get("position") or "") for job in _visible(cv.get("experience"))]
            for phrase in phrases + [str(basics.get("headline") or "")]:
                phrase = tx.one_line(phrase)
                if not phrase or len(phrase) > MAX_ROLE_CHARS or not tx.role_words_of(phrase):
                    continue
                if phrase.lower() not in seen:
                    seen.add(phrase.lower())
                    self.roles.append(phrase)
                if len(self.roles) == MAX_ROLES:
                    break
        self.role_words = []
        for role in self.roles:
            for word in tx.role_words_of(role):
                if word not in self.role_words:
                    self.role_words.append(word)
        self.locations = [place for place in (tx.one_line(item) for item in _strings(prefs.get("locations"))) if place]
        self.remote_ok = remote_allowed(prefs)
        self.skills = []
        for group in _visible(cv.get("skills")) if cv else []:
            self.skills.extend(skill for skill in (tx.one_line(item) for item in _strings(group.get("items"))) if skill)
        self.background = compact_profile(cv) if cv else ""

    def key(self):
        return (self.roles, self.role_words, self.locations, self.remote_ok, self.skills, self.background)

    def is_local(self, lowered):
        """Does a posting (lower case) belong to the user's place: it names a preferred location,
        or it offers remote work and the work mode allows it?"""
        return any(tx.mentions_lowered(lowered, place) for place in self.locations) or (self.remote_ok and offers_remote(lowered))

    def has_place(self):
        return bool(self.locations) or self.remote_ok

    def in_field(self, text):
        """Does a posting share a meaningful word with a role phrase? No roles: every posting does."""
        return tx.title_fits_roles(text, self.roles)

    def has_skill(self, skill):
        return skill.strip().lower() in [own.lower() for own in self.skills]

    def place_label(self):
        """"Ho Chi Minh City, Singapore or remote"."""
        parts = list(self.locations) + (["remote"] if self.remote_ok else [])
        if len(parts) <= 1:
            return parts[0] if parts else ""
        return ", ".join(parts[:-1]) + " or " + parts[-1]


def offers_remote(lowered):
    return (any(tx.mentions_lowered(lowered, word) for word in REMOTE_WORDS)
            and not any(tx.mentions_lowered(lowered, word) for word in NOT_REMOTE))


def load():
    """`(frame, preferences)` from the data folder. No profile yet: no background, and every
    posting is in the field."""
    try:
        cv = state.load_profile().get("cv")
    except Fail:
        cv = None
    prefs = state.read_json("preferences.json", {})
    prefs = prefs if isinstance(prefs, dict) else {}
    return MarketFrame(cv, prefs), prefs
