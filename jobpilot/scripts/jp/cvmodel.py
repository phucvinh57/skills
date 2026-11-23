"""The profile / CV schema as plain dicts.

`parse` is lenient the way the renderer is: every key may be missing, a scalar may be a number or a
bool (read as text), null reads as empty, unknown keys are dropped. What comes out always has every
key, so the guard, the trace check and the renderer never test for a missing one. The same shape
holds the stored profile and a tailored CV, which is what the fact guard compares.
"""

import re

SECTIONS = ("summary", "skills", "experience", "projects", "education", "certifications", "languages")
BASICS = ("name", "headline", "email", "phone", "location", "github", "linkedin", "website")
STYLE_KEYS = ("accent-color", "font", "font-size", "paper")
PAPERS = ("a4", "us-letter")
# What a style value may be: the template uses them as a colour, a font name, a size and a paper.
_STYLE = {
    "accent-color": (re.compile(r"^#[0-9a-fA-F]{6}$"), "a colour like #0092b8"),
    "font": (re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$"), "a font name"),
    "font-size": (re.compile(r"^\d{1,2}(\.\d)?pt$"), "a size like 10pt"),
    "paper": (re.compile("^(" + "|".join(PAPERS) + ")$"), "a4 or us-letter"),
}

# field name -> "text" | "texts" | "bool" | nested list schema
_GROUP = {"title": "text", "bullets": "texts"}
_ITEMS = {
    "skills": {"category": "text", "items": "texts", "hidden": "bool"},
    "experience": {
        "company": "text", "url": "text", "position": "text", "location": "text", "start": "text", "end": "text",
        "bullets": "texts", "groups": _GROUP, "hidden": "bool",
    },
    "projects": {
        "name": "text", "url": "text", "role": "text", "start": "text", "end": "text", "intro": "text",
        "bullets": "texts", "hidden": "bool",
    },
    "education": {
        "institution": "text", "degree": "text", "location": "text", "gpa": "text", "start": "text", "end": "text",
        "bullets": "texts", "hidden": "bool",
    },
    "certifications": {"name": "text", "issuer": "text", "url": "text", "date": "text", "hidden": "bool"},
    "languages": {"language": "text", "fluency": "text", "hidden": "bool"},
}


class CvError(Exception):
    """The value does not have the CV shape. The message names the place."""


def _scalar(value, path):
    """A scalar (string, number, bool) read as text; null reads as ""; lists/objects are refused."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise CvError(f"{path}: expected text, found a list or an object")


def _strings(value, path):
    """null -> [], a scalar -> [it], a list of scalars -> their texts."""
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for i, item in enumerate(value):
            if isinstance(item, (list, dict)):
                raise CvError(f"{path}[{i}]: expected text items, found a list or an object")
            out.append(_scalar(item, f"{path}[{i}]"))
        return out
    if isinstance(value, dict):
        raise CvError(f"{path}: expected a list of text")
    return [_scalar(value, path)]


def _bool(value, path):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise CvError(f"{path}: expected true or false")


def _object(value, path):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CvError(f"{path}: expected an object")
    return value


def _list(value, path):
    if value is None:
        return []
    if not isinstance(value, list):
        raise CvError(f"{path}: expected a list")
    return value


def _item(value, schema, path):
    if not isinstance(value, dict):
        raise CvError(f"{path}: expected an object")
    out = {}
    for key, kind in schema.items():
        raw, at = value.get(key), f"{path}.{key}"
        if kind == "text":
            out[key] = _scalar(raw, at)
        elif kind == "texts":
            out[key] = _strings(raw, at)
        elif kind == "bool":
            out[key] = _bool(raw, at)
        else:
            out[key] = [_item(entry, kind, f"{at}[{i}]") for i, entry in enumerate(_list(raw, at))]
    return out


def empty():
    return parse({})


def parse(value):
    """A CV or profile from decoded JSON. Raises `CvError` when the shape is wrong."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CvError("cv: expected a JSON object")
    basics = _object(value.get("basics"), "basics")
    cv = {"basics": {key: _scalar(basics.get(key), f"basics.{key}") for key in BASICS}}

    sections = _strings(value.get("sections"), "sections")
    for i, name in enumerate(sections):
        if name not in SECTIONS:
            raise CvError(f"sections[{i}]: `{name}` is not a section (one of: {', '.join(SECTIONS)})")
    cv["sections"] = sections
    titles = _object(value.get("titles"), "titles")
    cv["titles"] = {key: _scalar(titles[key], f"titles.{key}") for key in SECTIONS if key in titles}
    style = _object(value.get("style"), "style")
    cv["style"] = {}
    for key in STYLE_KEYS:
        text = _scalar(style.get(key), f"style.{key}").strip()
        if not text:
            continue
        pattern, what = _STYLE[key]
        if not pattern.match(text):
            raise CvError(f"style.{key}: `{text}` is not {what}")
        cv["style"][key] = text

    cv["summary"] = _strings(value.get("summary"), "summary")
    for name, schema in _ITEMS.items():
        cv[name] = [_item(entry, schema, f"{name}[{i}]") for i, entry in enumerate(_list(value.get(name), name))]
    return cv


def strip_layout(cv):
    """The profile form: no `sections`, `titles`, `style`, and nothing hidden."""
    cv["sections"], cv["titles"], cv["style"] = [], {}, {}
    for name in _ITEMS:
        for entry in cv[name]:
            entry["hidden"] = False
    return cv


def compact(value):
    """The same data without empty values ("" / [] / {} / false)."""
    if isinstance(value, dict):
        out = {key: compact(item) for key, item in value.items()}
        return {key: item for key, item in out.items() if item not in ("", [], {}, False, None)}
    if isinstance(value, list):
        return [item for item in (compact(item) for item in value) if item not in ("", [], {}, None)]
    return value


def tidy(value):
    """Collapse whitespace in every text and drop empty list items (the profile draft)."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return [item for item in (tidy(item) for item in value) if item != ""]
    if isinstance(value, dict):
        return {key: tidy(item) for key, item in value.items()}
    return value


def markup_fields(cv):
    """`(path, text)` of every field the renderer reads as markup: summary lines, bullets, group
    titles, project intros."""
    out = [(f"summary[{i}]", s) for i, s in enumerate(cv["summary"])]
    for i, e in enumerate(cv["experience"]):
        out += [(f"experience[{i}].bullets[{j}]", b) for j, b in enumerate(e["bullets"])]
        for j, g in enumerate(e["groups"]):
            out.append((f"experience[{i}].groups[{j}].title", g["title"]))
            out += [(f"experience[{i}].groups[{j}].bullets[{k}]", b) for k, b in enumerate(g["bullets"])]
    for i, p in enumerate(cv["projects"]):
        out.append((f"projects[{i}].intro", p["intro"]))
        out += [(f"projects[{i}].bullets[{j}]", b) for j, b in enumerate(p["bullets"])]
    for i, e in enumerate(cv["education"]):
        out += [(f"education[{i}].bullets[{j}]", b) for j, b in enumerate(e["bullets"])]
    return [(path, text) for path, text in out if text]


def prose(cv):
    """Every free-text value a reader sees (headline, summary, skills, bullets, intros, group
    titles, roles, degrees, fluency) as `(path, text)`. Used for the number check."""
    out = [("basics.headline", cv["basics"]["headline"])]
    out += [(f"summary[{i}]", s) for i, s in enumerate(cv["summary"])]
    for i, g in enumerate(cv["skills"]):
        out.append((f"skills[{i}].category", g["category"]))
        out += [(f"skills[{i}].items[{j}]", s) for j, s in enumerate(g["items"])]
    for i, e in enumerate(cv["experience"]):
        out.append((f"experience[{i}].position", e["position"]))
        out += [(f"experience[{i}].bullets[{j}]", b) for j, b in enumerate(e["bullets"])]
        for j, g in enumerate(e["groups"]):
            out.append((f"experience[{i}].groups[{j}].title", g["title"]))
            out += [(f"experience[{i}].groups[{j}].bullets[{k}]", b) for k, b in enumerate(g["bullets"])]
    for i, p in enumerate(cv["projects"]):
        out.append((f"projects[{i}].role", p["role"]))
        out.append((f"projects[{i}].intro", p["intro"]))
        out += [(f"projects[{i}].bullets[{j}]", b) for j, b in enumerate(p["bullets"])]
    for i, e in enumerate(cv["education"]):
        out.append((f"education[{i}].degree", e["degree"]))
        out.append((f"education[{i}].gpa", e["gpa"]))
        out += [(f"education[{i}].bullets[{j}]", b) for j, b in enumerate(e["bullets"])]
    for i, lang in enumerate(cv["languages"]):
        out.append((f"languages[{i}].fluency", lang["fluency"]))
    return [(path, text) for path, text in out if text]


def corpus(cv):
    """Every string of the CV, lower-cased and joined: "somewhere in the profile"."""
    b = cv["basics"]
    parts = [b[key] for key in BASICS]
    parts += cv["summary"]
    for g in cv["skills"]:
        parts.append(g["category"])
        parts += g["items"]
    for e in cv["experience"]:
        parts += [e["company"], e["url"], e["position"], e["location"], e["start"], e["end"]]
        parts += e["bullets"]
        for g in e["groups"]:
            parts.append(g["title"])
            parts += g["bullets"]
    for p in cv["projects"]:
        parts += [p["name"], p["url"], p["role"], p["start"], p["end"], p["intro"]]
        parts += p["bullets"]
    for e in cv["education"]:
        parts += [e["institution"], e["degree"], e["location"], e["gpa"], e["start"], e["end"]]
        parts += e["bullets"]
    for c in cv["certifications"]:
        parts += [c["name"], c["issuer"], c["url"], c["date"]]
    for lang in cv["languages"]:
        parts += [lang["language"], lang["fluency"]]
    return "\n".join(part for part in parts if part).lower()


def counts(cv):
    return {
        "summary": len(cv["summary"]),
        "skills": sum(len(g["items"]) for g in cv["skills"]),
        "experience": len(cv["experience"]),
        "projects": len(cv["projects"]),
        "education": len(cv["education"]),
        "certifications": len(cv["certifications"]),
        "languages": len(cv["languages"]),
    }
