"""`discover`: search the job sources and store what is new as leads."""
from .. import discover as engine
from ..cli import command, group


def _run(args):
    out = engine.run(source_id=args.source, limit=args.max)
    out["next"] = ("rate each lead in `to_score`: `lead show ID`, then `lead score ID --file score.json` "
                   "(references/discover.md)") if out["to_score"] else "nothing waits for a score"
    return out


def register(groups):
    commands = group(groups, "discover", "Search the job sources for new postings.")
    run = command(commands, "run", _run, "Search every enabled job source (or one) and save the new postings as leads.")
    run.add_argument("--source", type=int, help="only this source (its id)")
    run.add_argument("--max", type=int, help="most new postings read per source (default 75)")
