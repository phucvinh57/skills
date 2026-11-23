"""`docs`: the user's own documents (an old CV, a LinkedIn export, notes). The profile is read
from these and from nothing else."""
import os
import sys

from .. import ingest, state
from ..cli import Fail, command, group

MAX_CHARS = 20_000
MAX_TOTAL_CHARS = 60_000
MAX_NAME_CHARS = 80
INDEX = os.path.join("documents", "index.json")


def index():
    return state.read_json(INDEX, [])


def documents():
    """Every stored document as `(entry, text)`, in the order they were added."""
    return [(entry, state.read_text(os.path.join("documents", entry["file"]), "")) for entry in index()]


def documents_hash(entries=None):
    """Changes whenever a document is added, removed or replaced."""
    entries = index() if entries is None else entries
    return state.text_hash("\n".join(f"{entry['name']}\t{entry['hash']}" for entry in entries))


def corpus_text():
    return "\n\n".join(text for _, text in documents())


def _add(args):
    if bool(args.file) == bool(args.text):
        raise Fail("give one of --file or --text")
    if args.file and args.file != "-":
        text, kind = ingest.read_file(args.file), "file"
        name = args.name or os.path.basename(args.file)
    else:
        text, kind = ingest.clean(sys.stdin.read() if args.file == "-" else args.text), "note"
        name = args.name or "Note"
        if not text.strip():
            raise Fail("the note is empty")
    name = " ".join(name.split())[:MAX_NAME_CHARS] or "Note"
    text, truncated = ingest.truncate(text, MAX_CHARS)

    entries = index()
    previous = next((entry for entry in entries if entry["name"].lower() == name.lower()), None)
    others = [entry for entry in entries if entry is not previous]
    total = sum(entry["chars"] for entry in others) + len(text)
    if total > MAX_TOTAL_CHARS:
        raise Fail(
            f"the documents would hold {total} characters; the limit is {MAX_TOTAL_CHARS}",
            hint="remove a document first (`docs remove NAME`) or add a shorter one",
        )
    if previous:
        file_name = previous["file"]
    else:
        base = state.slugify(os.path.splitext(name)[0], fallback="document")
        used = {entry["file"] for entry in entries}
        file_name, n = f"{base}.txt", 2
        while file_name in used:
            file_name, n = f"{base}-{n}.txt", n + 1
    state.write_text(os.path.join("documents", file_name), text)
    entry = {"name": name, "kind": kind, "file": file_name, "chars": len(text), "truncated": truncated,
             "added_at": state.now(), "hash": state.text_hash(text)}
    state.write_json(INDEX, others + [entry] if not previous else [entry if e is previous else e for e in entries])
    out = {"added": entry, "replaced": bool(previous), "documents": len(others) + 1, "total_chars": total,
           "next": "read the profile again: `docs show`, then `profile check` (references/profile.md)"}
    if truncated:
        out["warning"] = f"the document was cut to {MAX_CHARS} characters; split it and add the rest as another document"
    return out


def _list(args):
    entries = index()
    return {"documents": entries, "total_chars": sum(entry["chars"] for entry in entries),
            "limits": {"per_document": MAX_CHARS, "total": MAX_TOTAL_CHARS}}


def _remove(args):
    entries = index()
    wanted = args.name.strip().lower()
    found = next((entry for entry in entries if wanted in (entry["name"].lower(), entry["file"].lower())), None)
    if not found:
        raise Fail(f"no document named {args.name!r}", hint="`docs list` shows the names")
    state.write_json(INDEX, [entry for entry in entries if entry is not found])
    try:
        os.unlink(state.path("documents", found["file"]))
    except OSError:
        pass
    return {"removed": found["name"], "documents": len(entries) - 1,
            "next": "the profile is now out of date: read it again (`docs show`, then `profile check`)"}


def _show(args):
    stored = documents()
    if not stored:
        raise Fail("there are no documents yet", hint="add one: `docs add --file PATH` or `docs add --text TEXT`")
    fenced = [state.fence("document", text.rstrip("\n"), n=i + 1, kind=entry["kind"], name=entry["name"])
              for i, (entry, text) in enumerate(stored)]
    return {"note": state.UNTRUSTED_NOTE, "count": len(stored), "documents": "\n\n".join(fenced)}


def register(groups):
    commands = group(groups, "docs", "The user's documents: the only source of facts for the profile.")
    add = command(commands, "add", _add, "Add a document (.pdf, .md, .txt, .html) or a typed note.")
    add.add_argument("--file", help="path of the document (`-` reads a note from stdin)")
    add.add_argument("--text", help="a note, as text")
    add.add_argument("--name", help="how the document is called (default: the file name, or Note)")
    command(commands, "list", _list, "The stored documents.")
    remove = command(commands, "remove", _remove, "Remove a document by name.")
    remove.add_argument("name")
    command(commands, "show", _show, "Every document between markers: what the profile is drafted from.")
