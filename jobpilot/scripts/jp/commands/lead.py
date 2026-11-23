"""`lead`: the jobs discovery found: list, show (with what rating needs), score, dismiss, restore, promote."""
from .. import leads, prefs as prefs_model, score as scoring, state
from ..cli import Fail, command, group, read_json_arg


def _list(args):
    rows = leads.listed(args.state, unscored=args.unscored, show_hidden=args.all)
    out = {"state": args.state, "count": len(rows), "leads": [leads.row_view(lead) for lead in rows]}
    if args.state == "new" and not args.all:
        hidden = len([lead for lead in leads.load() if lead.get("state") == "new" and lead.get("filtered_reason")])
        if hidden:
            out["hidden_by_preferences"] = hidden
    return out


def _show(args):
    lead = leads.get(args.id)
    view = {name: value for name, value in lead.items() if name != "snippet"}
    prefs = prefs_model.load()
    wanted = {name: value for name, value in prefs.items() if name != "weights"}
    try:
        profile = scoring.compact_profile(state.load_profile()["cv"])
    except Fail:
        profile = None
    header = "Title: {}\nCompany: {}\nLocation: {}\n\n".format(
        scoring.one_line(lead.get("role", ""), 200), scoring.one_line(lead.get("company", ""), 100),
        scoring.one_line(lead.get("location", ""), 100))
    posting = header + leads.posting_text(lead)[:scoring.SCORE_JD_CHARS]
    return {
        "lead": view,
        "scoring": {
            "profile": profile,
            "preferences": wanted,
            "note": state.UNTRUSTED_NOTE,
            "posting": state.fence("posting", posting),
            "answer": f"jobpilot.py lead score {lead['id']} --file score.json",
        },
    }


def _score(args):
    lead = leads.set_score(args.id, read_json_arg(args.file))
    out = {"id": lead["id"], "score": lead["score"], "state": lead["state"], "weights": lead["score_json"]["weights"],
           "why": scoring.top_reasons(lead["score_json"], 3)}
    if lead["state"] == "dismissed":
        out["dismiss_reason"] = lead["dismiss_reason"]
    if lead["score"] is None:
        out["note"] = "no criterion with a weight could be judged: the lead has no score"
    return out


def _dismiss(args):
    return {"lead": leads.row_view(leads.dismiss(args.id, args.reason or ""))}


def _restore(args):
    lead, hidden, excluded = leads.restore(args.id)
    out = {"lead": leads.row_view(lead)}
    if hidden:
        out["hidden"] = hidden
        out["note"] = "restored, but the preferences keep it out of the queue until they let it pass"
    if excluded:
        out["excluded"] = "its company is still in exclude_companies: later searches keep leaving it out"
    return out


def _promote(args):
    lead, job, created = leads.promote(args.id)
    return {"lead": leads.row_view(lead), "job": job, "created": created,
            "jd": f"jobs/{job['slug']}/jd.md",
            "next": "write the CV for this job: references/tailor.md"}


def register(groups):
    commands = group(groups, "lead", "Found jobs: list, show, score, dismiss, restore, promote.")
    listing = command(commands, "list", _list, "The leads of a state; new ones best score first.")
    listing.add_argument("--state", choices=leads.STATES, default="new")
    listing.add_argument("--unscored", action="store_true", help="only the new leads without a score")
    listing.add_argument("--all", action="store_true", help="also the new leads the preferences hide")
    command(commands, "show", _show, "One lead, with what rating it needs: profile, preferences, the posting."
            ).add_argument("id", type=int)
    score = command(commands, "score", _score, "Store the rating of a lead; the total is computed here.")
    score.add_argument("id", type=int)
    score.add_argument("--file", required=True, help="the five criteria and eligibility as JSON, or - for stdin")
    dismiss = command(commands, "dismiss", _dismiss, "Dismiss a lead (it is kept, so it is not found again).")
    dismiss.add_argument("id", type=int)
    dismiss.add_argument("--reason", default="")
    command(commands, "restore", _restore, "A dismissed lead is new again.").add_argument("id", type=int)
    command(commands, "promote", _promote, "Turn a lead into a draft job (only when the user asks)."
            ).add_argument("id", type=int)
