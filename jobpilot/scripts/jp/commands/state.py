"""`state`: where the data is, an overview, and one zip to carry it between sessions."""
import os
import zipfile

from .. import state, typst
from ..cli import Fail, command, group


def _info(args):
    jobs = state.list_jobs()
    leads = state.read_json("leads.json", [])
    profile = state.read_json("profile.json")
    by_status = {}
    for job in jobs:
        by_status[job["status"]] = by_status.get(job["status"], 0) + 1
    return {
        "data_dir": state.data_dir(),
        "profile": bool(profile),
        "documents": len(state.read_json("documents/index.json", [])),
        "preferences": bool(state.read_json("preferences.json")),
        "job_sources": len([s for s in state.read_json("sources.json", []) if s.get("purpose", "jobs") == "jobs"]),
        "leads_new": len([lead for lead in leads if lead.get("state") == "new"]),
        "jobs": by_status,
        "pdf": "typst" if typst.available() else "none (HTML only): " + typst.INSTALL_HINT,
    }


def _export(args):
    root = state.data_dir()
    target = os.path.abspath(args.out)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, _, files in os.walk(root):
            for name in files:
                full = os.path.join(folder, name)
                if os.path.abspath(full) == target or name.startswith(".tmp-"):
                    continue
                archive.write(full, os.path.relpath(full, root))
    return {"saved": target, "note": "give this file to the user; `state import` restores it in a new session"}


def _import(args):
    root = state.data_dir()
    if not os.path.isfile(args.file):
        raise Fail(f"no such file: {args.file}")
    if os.listdir(root) and not args.replace:
        raise Fail("the data folder is not empty", hint="--replace overwrites files with the same name (ask the user first)")
    restored = 0
    with zipfile.ZipFile(args.file) as archive:
        for member in archive.infolist():
            name = member.filename
            parts = name.split("/")
            if member.is_dir() or name.startswith("/") or ".." in parts or "\\" in name or ":" in name:
                continue
            destination = os.path.join(root, *parts)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(member) as source, open(destination, "wb") as out:
                out.write(source.read())
            restored += 1
    return {"restored": restored, "data_dir": root}


def register(groups):
    commands = group(groups, "state", "Where JobPilot's data is; export and import it as one zip.")
    command(commands, "info", _info, "The data folder and what is in it.")
    export = command(commands, "export", _export, "Save all data as one zip.")
    export.add_argument("--out", default="jobpilot-data.zip")
    restore = command(commands, "import", _import, "Restore a zip made by `state export`.")
    restore.add_argument("file")
    restore.add_argument("--replace", action="store_true")
