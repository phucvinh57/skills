"""`prefs`: what the user looks for. Changed with a patch (only the fields that change), one step of undo."""
from .. import prefs as model
from ..cli import Fail, command, group, read_json_arg


def _show(args):
    return {"saved": model.exists(), "preferences": model.load(), "can_undo": model.can_undo()}


def _patch(args):
    current, existed = model.load(), model.exists()
    merged = model.merge_patch(current, read_json_arg(args.file))
    saved, changed = model.update(merged)
    return {
        "preferences": saved,
        "changed": merged != current or not existed,
        "leads": changed,
        "summary": model.summary(saved),
        "can_undo": model.can_undo(),
    }


def _undo(args):
    done = model.undo()
    if done is None:
        raise Fail("there is no change to undo", hint="only the last change can be undone")
    restored, changed = done
    return {"preferences": restored, "leads": changed, "summary": model.summary(restored)}


def _summary(args):
    prefs = model.load()
    lines = model.summary(prefs)
    return {"saved": model.exists(), "summary": lines, "text": "\n".join(lines) or "No preferences yet."}


def register(groups):
    commands = group(groups, "prefs", "What the user looks for: show, change with a patch, undo, summary.")
    command(commands, "show", _show, "The saved preferences (the default document when none were saved).")
    patch = command(commands, "patch", _patch, "Apply a patch: a JSON object with only the fields that change.")
    patch.add_argument("--file", required=True, help="the patch as a JSON file, or - for stdin")
    command(commands, "undo", _undo, "Put back the preferences from before the last change.")
    command(commands, "summary", _summary, "A short summary to show the user.")
