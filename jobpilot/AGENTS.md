# JobPilot

One Agent Skill (this folder, `jobpilot/` in the skills repository): the user's documents -> profile -> tailored CV (every fact checked) ->
job discovery and scoring -> apply help -> status tracking -> market trends. The host agent is the LLM; the
Python scripts do everything that must not be left to a model. There is no app, no server and no API key.

## Layout

Paths are relative to this folder.

```
jobpilot/
  SKILL.md               frontmatter (name, description), the rules, a table that routes to references/
  references/            instructions for the host agent, one file per workflow, read on demand:
                         profile, tailor, preferences, discover, apply, track, trends, schemas
  assets/                cv.typ (self-contained, no package import), trends.typ, example-cv.json
  scripts/jobpilot.py    the one entry point: `jobpilot.py <group> <command>`, JSON out, `{"error","hint"}` + exit 1
  scripts/jp/
    cli        finds every module in `commands/` (`register(groups)`), `Fail`, argument helpers
    state      the data folder (`JOBPILOT_DATA_DIR`, else `~/.jobpilot`), atomic writes, jobs, timeline,
               `can_advance_to` (statuses only move forward), `fence` (untrusted text between markers)
    http       the one HTTP client: public hosts only (connection pinned to the checked address), 1 s per host,
               size cap, redirects followed by hand and checked
    ingest     documents and pages -> clean text (md, txt, html; pdf through pypdf or pdftotext when present)
    typst      compile through the typst program or the `typst` Python package
    dedup      canonical address, company+role key, `find_known`
    cvmodel, trace, guard, render    the CV schema; documents -> kept facts + `left_out`; profile -> violations;
               markup (`**bold**`, `_italic_`, `[text](url)`) -> Typst and HTML
    prefs, score, region, leads, discover, insight    preferences, hard filters, the weighted total, the run,
               the company brief grounded in the texts read
    sources/   greenhouse, lever, ashby, smartrecruiters, recruitee, personio, workday, hn_hiring, yc,
               career_page (jsonld, sitemap, web, boards)
    answers, applyhelp, mailmatch    remembered answers, form prefill and validation, email -> job candidates
    trends/    period, collectors (hackernews, yc, rss, page, person), frame, count, claims, brief, cite, report
    commands/  one module per group: state, docs, profile, job, cv, prefs, source, discover, lead, brief,
               apply, answers, track, trends
  tests/                 unittest, standard library only; `support.py` (`DataDirCase`, `Loopback` fixture server),
                         `validate_skill.py` (the skill's format, run by `test_skill.py`)
  README.md, AGENTS.md (CLAUDE.md links here), REVIEW.md    for people and agents developing the skill
```

## Commands

Run from this folder.

- Tests and the gate before a merge (there is no CI): `python3 -m unittest discover -s tests -t .` (no network; the PDF tests are
  skipped without Typst; `test_skill.py` checks the skill's format).
- Try the skill by hand: `JOBPILOT_DATA_DIR=/tmp/jp python3 scripts/jobpilot.py state info`.

## Rules

- Never add facts to a CV that are not in the user's documents. The agent drafts; `trace` keeps only what the
  documents state and `guard` rejects a CV the profile does not back. Code never writes a fact into the
  profile, and no command edits one: the user changes a document and the profile is read again.
- A check is never weakened to let a draft through. A change to `trace.py`, `guard.py` or `cite.py` comes with
  a test that shows what it now refuses or accepts.
- The agent supplies judgments, the scripts compute: the lead total, the citation check, the status move and
  the form validation are code. A value the agent sends for something code computes is ignored.
- Text from outside (documents, postings, pages, feeds, emails, form labels) is printed only through
  `state.fence` with `state.UNTRUSTED_NOTE`. Reference files tell the agent it is data.
- Outgoing HTTP goes through `jp.http` only. `TEST_ALLOW_PRIVATE` is for tests; no command or environment
  variable sets it.
- A login, captcha or block page is never solved, evaded or waited out. A source that needs a browser raises
  `NeedsBrowser`; the agent tells the user.
- Nothing is submitted without the user's explicit yes for that job. The scripts never submit anything.
- Statuses only move forward (`state.can_advance_to`); `set_status(..., force=True)` is the user's own
  correction.
- Leads are not jobs: a lead becomes a job only through `lead promote` or `job add`.
- Standard library only, Python 3.9 syntax. An optional import (`pypdf`, `typst`) is guarded and has a
  fallback. `tests/validate_skill.py` enforces both.
- Data is written only through `state.write_json` / `state.write_text`. No secret is stored or logged.
- A command returns JSON; an error the user or agent can act on is `Fail(message, hint=...)`, never a traceback.
- Reference files are the prompts: a human reads their diff. Keep them short, exact about commands, and in
  step with the scripts (a changed command changes its reference in the same commit).

## Workflow

- Work on a branch, open a draft PR, show the output of the tests.
- `REVIEW.md` lists the failures to look for and which paths need a human to read the diff.
- A change to `README.md` alone needs no PR.
