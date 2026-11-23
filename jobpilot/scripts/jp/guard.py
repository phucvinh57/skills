"""Fact guard: a deterministic check that a tailored CV only states facts the stored profile has.

The agent writes the CV; this is the safety net. It checks identity (name, contact, links),
structure (every employer + title + dates, project, school, certification and language exists in
the profile), every number a reader sees, every link target and every skill item. It does not judge
prose: rewording a bullet is allowed, inventing a number or an employer is not.
"""
import re

from . import cvmodel, render

# Terms the guard treats as the same skill (all lower case).
SYNONYMS = (
    ("k8s", "kubernetes"),
    ("js", "javascript"),
    ("ts", "typescript"),
    ("postgres", "postgresql"),
    ("golang", "go"),
    ("mongo", "mongodb"),
    ("node", "nodejs", "node.js"),
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _violation(path, message):
    return {"path": path, "message": message}


def guard(profile, cv):
    """Check `cv` against the stored `profile` (both parsed by `cvmodel.parse`).
    Returns violations `{"path", "message"}`; empty = the CV is clean."""
    out = []
    _check_basics(profile, cv, out)
    _check_experience(profile, cv, out)
    _check_projects(profile, cv, out)
    _check_education(profile, cv, out)
    _check_certifications(profile, cv, out)
    _check_languages(profile, cv, out)
    corpus = cvmodel.corpus(profile)
    _check_numbers(corpus, cv, out)
    _check_links(corpus, cv, out)
    _check_skills(corpus, cv, out)
    return out


def format_violations(violations):
    return "\n".join(f"- {v['path']}: {v['message']}" for v in violations)


# ---- normalisation ------------------------------------------------------------------------------

def norm(text):
    """Case and whitespace-insensitive form."""
    return " ".join(text.split()).lower()


def norm_url(text):
    """A link without scheme, `www.` or trailing slash."""
    text = norm(text)
    for scheme in ("https://", "http://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
            break
    if text.startswith("www."):
        text = text[4:]
    return text.rstrip("/")


def norm_phone(text):
    return "".join(c for c in text if c in "0123456789+")


# ---- identity -----------------------------------------------------------------------------------

def _check_basics(profile, cv, out):
    p, c = profile["basics"], cv["basics"]
    if norm(c["name"]) != norm(p["name"]):
        out.append(_violation("basics.name", f"must be `{p['name']}` (the profile name), found `{c['name']}`"))
    for key, form in (("email", norm), ("phone", norm_phone), ("github", norm_url), ("linkedin", norm_url),
                      ("website", norm_url)):
        if c[key].strip() and form(c[key]) != form(p[key]):
            shown = f"`{p[key]}`" if p[key].strip() else "empty in the profile"
            out.append(_violation(f"basics.{key}", f"must match the profile ({shown}), found `{c[key]}`"))
    if c["location"].strip():
        known = {norm(p["location"])}
        known |= {norm(e["location"]) for e in profile["experience"]}
        known |= {norm(e["location"]) for e in profile["education"]}
        known.discard("")
        if norm(c["location"]) not in known:
            out.append(_violation(
                "basics.location",
                f"`{c['location']}` is not a location in the profile (profile: `{p['location']}`)",
            ))


# ---- structure ----------------------------------------------------------------------------------

def _field_matches(path, label, cv_value, profile_value, out):
    """A non-empty CV value must equal the profile's (case/whitespace-insensitive)."""
    if not cv_value.strip() or norm(cv_value) == norm(profile_value):
        return
    out.append(_violation(path, f"{label} `{cv_value}` differs from the profile (`{profile_value}`)"))


def _url_matches(path, cv_value, profile_value, out):
    if cv_value.strip() and norm_url(cv_value) != norm_url(profile_value):
        out.append(_violation(path, f"url `{cv_value}` differs from the profile (`{profile_value}`)"))


def _check_experience(profile, cv, out):
    for i, e in enumerate(cv["experience"]):
        same_company = [p for p in profile["experience"] if norm(p["company"]) == norm(e["company"])]
        if not same_company:
            out.append(_violation(f"experience[{i}].company", f"`{e['company']}` is not an employer in the profile"))
            continue
        exact = next((p for p in same_company
                      if norm(p["position"]) == norm(e["position"]) and norm(p["start"]) == norm(e["start"])
                      and norm(p["end"]) == norm(e["end"])), None)
        if exact:
            _field_matches(f"experience[{i}].location", "location", e["location"], exact["location"], out)
            _url_matches(f"experience[{i}].url", e["url"], exact["url"], out)
        else:
            known = "; ".join(f"`{p['position']}` {p['start']} - {p['end']}" for p in same_company)
            out.append(_violation(
                f"experience[{i}].position",
                f"`{e['position']}` {e['start']} - {e['end']} at {e['company']} does not match the profile ({known})",
            ))


def _find(items, key, value):
    return next((item for item in items if norm(item[key]) == norm(value)), None)


def _check_projects(profile, cv, out):
    for i, p in enumerate(cv["projects"]):
        src = _find(profile["projects"], "name", p["name"])
        if not src:
            out.append(_violation(f"projects[{i}].name", f"`{p['name']}` is not a project in the profile"))
            continue
        for field in ("role", "start", "end"):
            _field_matches(f"projects[{i}].{field}", field, p[field], src[field], out)
        _url_matches(f"projects[{i}].url", p["url"], src["url"], out)


def _check_education(profile, cv, out):
    for i, e in enumerate(cv["education"]):
        src = _find(profile["education"], "institution", e["institution"])
        if not src:
            out.append(_violation(f"education[{i}].institution", f"`{e['institution']}` is not a school in the profile"))
            continue
        for field in ("degree", "location", "gpa", "start", "end"):
            _field_matches(f"education[{i}].{field}", field, e[field], src[field], out)


def _check_certifications(profile, cv, out):
    for i, c in enumerate(cv["certifications"]):
        src = _find(profile["certifications"], "name", c["name"])
        if not src:
            out.append(_violation(f"certifications[{i}].name", f"`{c['name']}` is not a certification in the profile"))
            continue
        _field_matches(f"certifications[{i}].issuer", "issuer", c["issuer"], src["issuer"], out)
        _field_matches(f"certifications[{i}].date", "date", c["date"], src["date"], out)
        _url_matches(f"certifications[{i}].url", c["url"], src["url"], out)


def _check_languages(profile, cv, out):
    for i, lang in enumerate(cv["languages"]):
        src = _find(profile["languages"], "language", lang["language"])
        if not src:
            out.append(_violation(f"languages[{i}].language", f"`{lang['language']}` is not a language in the profile"))
            continue
        _field_matches(f"languages[{i}].fluency", "fluency", lang["fluency"], src["fluency"], out)


# ---- numbers and links --------------------------------------------------------------------------

def number_tokens(text):
    """Digit tokens of `text`: `5000+` -> `5000`, `~13MB` -> `13`, `1,000` -> `1000`, `3.5` -> `3.5`."""
    return [match.group(0).replace(",", "") for match in _NUMBER.finditer(text)]


def _check_numbers(profile_corpus, cv, out):
    known = set(number_tokens(profile_corpus))
    markup = {path for path, _ in cvmodel.markup_fields(cv)}
    for path, text in cvmodel.prose(cv):
        # a link target is not prose: it is checked as a link below
        shown = render.without_link_targets(text) if path in markup else text
        for token in number_tokens(shown):
            if token not in known:
                out.append(_violation(path, f"number `{token}` does not appear in the profile"))


def _check_links(profile_corpus, cv, out):
    for path, text in cvmodel.markup_fields(cv):
        for target in render.link_targets(text):
            wanted = norm_url(target)
            if not wanted or wanted not in profile_corpus:
                out.append(_violation(path, f"link `{target}` does not appear in the profile"))


# ---- skills -------------------------------------------------------------------------------------

def contains_word(hay, needle):
    """Occurrence of `needle` in `hay` not touching other letters/digits (for 1-2 letter terms like `Go`)."""
    if not needle:
        return True
    start = hay.find(needle)
    while start != -1:
        end = start + len(needle)
        before = hay[start - 1] if start > 0 else ""
        after = hay[end] if end < len(hay) else ""
        if not (before and before.isalnum()) and not (after and after.isalnum()):
            return True
        start = hay.find(needle, start + 1)
    return False


def term_in_corpus(corpus, term):
    term = norm(term)
    if not term:
        return True
    candidates = [term]
    for group in SYNONYMS:
        if term in group:
            candidates.extend(group)
    return any(contains_word(corpus, c) if len(c) <= 2 else c in corpus for c in candidates)


def _check_skills(profile_corpus, cv, out):
    for i, group in enumerate(cv["skills"]):
        for j, item in enumerate(group["items"]):
            if not term_in_corpus(profile_corpus, item):
                out.append(_violation(f"skills[{i}].items[{j}]", f"skill `{item}` does not appear in the profile"))
