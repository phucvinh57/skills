"""`profile`: the profile read from the user's documents. A draft is checked against the documents
and only what they support is kept. There is no command that writes a single fact."""
from .. import cvmodel, state
from ..cli import Fail, command, group, read_json_arg
from ..guard import norm
from ..trace import trace
from . import docs

FIX = "to add a missing fact: put it in a document (`docs add`), then draft and check the profile again"


def dedup_skills(cv):
    """A skill listed twice (in any group) is kept once, where it first appears."""
    seen = set()
    for skill_group in cv["skills"]:
        kept = []
        for item in skill_group["items"]:
            if norm(item) not in seen:
                seen.add(norm(item))
                kept.append(item)
        skill_group["items"] = kept
    cv["skills"] = [skill_group for skill_group in cv["skills"] if skill_group["items"]]
    return cv


def bare_links(cv):
    """Links without scheme or trailing slash: the renderer adds `https://` itself."""
    def bare(url):
        url = url.strip()
        for scheme in ("https://", "http://"):
            if url.startswith(scheme):
                url = url[len(scheme):]
                break
        return url.rstrip("/")

    for key in ("github", "linkedin", "website"):
        cv["basics"][key] = bare(cv["basics"][key])
    for section in ("experience", "projects", "certifications"):
        for entry in cv[section]:
            entry["url"] = bare(entry["url"])
    return cv


def build(draft, documents):
    """A draft (decoded JSON) -> `(profile, left_out)`: tidied, de-duplicated, traced, links bare."""
    cv = cvmodel.strip_layout(cvmodel.parse(cvmodel.tidy(draft)))
    cv, left_out = trace(dedup_skills(cv), documents)
    return bare_links(cv), left_out


def _check(args):
    stored = docs.documents()
    if not stored:
        raise Fail("there are no documents to check the draft against", hint="add the user's documents first (`docs add`)")
    draft = read_json_arg(args.file)
    if isinstance(draft, dict) and "basics" not in draft and isinstance(draft.get("cv"), dict):
        draft = draft["cv"]
    try:
        cv, left_out = build(draft, docs.corpus_text())
    except cvmodel.CvError as err:
        raise Fail(f"the draft does not have the profile shape: {err}", hint="see references/schemas.md")
    saved = state.write_json("profile.json", {
        "cv": cvmodel.compact(cv), "left_out": left_out, "built_at": state.now(),
        "documents_hash": docs.documents_hash(),
    })
    out = {"saved": saved, "counts": cvmodel.counts(cv), "left_out": left_out, "dropped": bool(left_out)}
    if not cv["basics"]["name"]:
        out["warning"] = "the profile has no name: the documents do not contain the name in the draft"
    if left_out:
        out["tell_the_user"] = "these facts were not found in the documents and were left out; " + FIX
    return out


def _show(args):
    profile = state.load_profile()
    out = {"cv": cvmodel.compact(profile["cv"]), "left_out": profile.get("left_out", []),
           "built_at": profile.get("built_at")}
    if profile.get("documents_hash") != docs.documents_hash():
        out["stale"] = True
        out["hint"] = "the documents changed since the profile was read: `docs show`, draft it again, `profile check`"
    return out


def register(groups):
    commands = group(groups, "profile", "The profile read from the user's documents.")
    check = command(commands, "check", _check, "Check a draft profile against the documents and save what they support.")
    check.add_argument("--file", required=True, help="the draft profile as JSON (`-` for stdin)")
    command(commands, "show", _show, "The stored profile, what was left out, and whether it is out of date.")
