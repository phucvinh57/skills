"""Trace check: a deterministic check that every fact in a profile draft is in the user's documents.

It is the fact guard (`guard.py`) one step earlier: the guard keeps a CV to the profile; this keeps
the profile to the documents.

Names, titles, employers, schools, dates, contact details, links and skills must appear in the text
(case and spacing aside); every number in prose must appear in the text. Prose itself may be
reworded. What fails is not kept: an entry without its employer, school or name is dropped, a field
is cleared, a line with an unknown number is dropped. Each is reported in plain words so the user
can add the fact to a document.
"""
import re

from .guard import contains_word, norm, norm_phone, norm_url, number_tokens, term_in_corpus

_PHONE = re.compile(r"\+?\d[\d\s().\-/]{5,}\d")


class Corpus:
    """What the user's documents say, normalised for lookups."""

    def __init__(self, text):
        self.text = norm(text)
        self.numbers = set(number_tokens(text))
        self.phones = [norm_phone(match.group(0)) for match in _PHONE.finditer(text)]

    def has(self, value):
        """`value` (or nothing) is in the text as whole words: `MIT` is not found in "submitted"."""
        value = norm(value)
        return not value or contains_word(self.text, value)

    def has_url(self, value):
        value = norm_url(value)
        return not value or value in self.text

    def has_phone(self, value):
        value = norm_phone(value)
        return not value or any(value.lstrip("+") in phone.lstrip("+") for phone in self.phones)

    def unknown_number(self, text):
        """The first number of `text` that is not in the documents."""
        return next((n for n in number_tokens(text) if n not in self.numbers), None)


def trace(cv, documents):
    """Keep only what `documents` support. Returns `(kept profile, left_out lines)`. Changes `cv`."""
    corpus = Corpus(documents)
    left_out = []

    def out(line):
        if line not in left_out:
            left_out.append(line)

    def clear_untraced(at, entry, fields):
        for what, key in fields:
            if not corpus.has(entry[key]):
                out(f"The {what} of {at}: “{entry[key]}”")
                entry[key] = ""

    def known_lines(at, lines, message="A line about {at} with the number {n}"):
        """Drop the lines that state a number the documents do not have."""
        kept = []
        for line in lines:
            number = corpus.unknown_number(line)
            if number is None:
                kept.append(line)
            else:
                out(message.format(at=at, n=number))
        return kept

    # ---- identity
    b = cv["basics"]
    for label, key, found in (("Name", "name", corpus.has), ("Email", "email", corpus.has),
                              ("Location", "location", corpus.has), ("GitHub", "github", corpus.has_url),
                              ("LinkedIn", "linkedin", corpus.has_url), ("Website", "website", corpus.has_url),
                              ("Phone", "phone", corpus.has_phone)):
        if not found(b[key]):
            out(f"{label} “{b[key]}”")
            b[key] = ""
    number = corpus.unknown_number(b["headline"])
    if number is not None:
        out(f"Headline “{b['headline']}” (the number {number})")
        b["headline"] = ""

    # ---- summary
    cv["summary"] = known_lines("", cv["summary"], "A summary line with the number {n}")

    # ---- skills
    for group in cv["skills"]:
        kept = []
        for item in group["items"]:
            if term_in_corpus(corpus.text, item):
                kept.append(item)
            else:
                out(f"Skill “{item}”")
        group["items"] = kept
    cv["skills"] = [group for group in cv["skills"] if group["items"]]

    # ---- experience
    roles = []
    for e in cv["experience"]:
        if not e["company"].strip() or not corpus.has(e["company"]) or not corpus.has(e["position"]):
            out(f"Role “{e['position']}” at “{e['company']}”")
            continue
        at = e["company"]
        clear_untraced(at, e, (("dates", "start"), ("dates", "end"), ("location", "location")))
        if not corpus.has_url(e["url"]):
            out(f"Link of {at}: “{e['url']}”")
            e["url"] = ""
        e["bullets"] = known_lines(at, e["bullets"])
        for group in e["groups"]:
            group["bullets"] = known_lines(at, group["bullets"])
        e["groups"] = [group for group in e["groups"] if group["bullets"]]
        roles.append(e)
    cv["experience"] = roles

    # ---- projects
    projects = []
    for p in cv["projects"]:
        if not p["name"].strip() or not corpus.has(p["name"]):
            out(f"Project “{p['name']}”")
            continue
        at = p["name"]
        clear_untraced(at, p, (("role", "role"), ("dates", "start"), ("dates", "end")))
        if not corpus.has_url(p["url"]):
            out(f"Link of {at}: “{p['url']}”")
            p["url"] = ""
        number = corpus.unknown_number(p["intro"])
        if number is not None:
            out(f"A line about {at} with the number {number}")
            p["intro"] = ""
        p["bullets"] = known_lines(at, p["bullets"])
        projects.append(p)
    cv["projects"] = projects

    # ---- education
    schools = []
    for e in cv["education"]:
        if not e["institution"].strip() or not corpus.has(e["institution"]):
            out(f"School “{e['institution']}”")
            continue
        at = e["institution"]
        clear_untraced(at, e, (("degree", "degree"), ("location", "location"), ("dates", "start"), ("dates", "end")))
        if e["gpa"] and (not corpus.has(e["gpa"]) or corpus.unknown_number(e["gpa"]) is not None):
            out(f"GPA of {at}: “{e['gpa']}”")
            e["gpa"] = ""
        e["bullets"] = known_lines(at, e["bullets"])
        schools.append(e)
    cv["education"] = schools

    # ---- certifications
    certifications = []
    for c in cv["certifications"]:
        if not c["name"].strip() or not corpus.has(c["name"]):
            out(f"Certification “{c['name']}”")
            continue
        clear_untraced(c["name"], c, (("issuer", "issuer"), ("date", "date")))
        if not corpus.has_url(c["url"]):
            c["url"] = ""
        certifications.append(c)
    cv["certifications"] = certifications

    # ---- languages
    languages = []
    for lang in cv["languages"]:
        if not lang["language"].strip() or not corpus.has(lang["language"]):
            out(f"Language “{lang['language']}”")
            continue
        clear_untraced(lang["language"], lang, (("level", "fluency"),))
        languages.append(lang)
    cv["languages"] = languages

    return cv, left_out
