# JobPilot

A job-search skill for AI agents that never invents facts about you.

You give it your own documents (a CV, a LinkedIn export, notes). It reads them into a profile, tailors a CV to
each job, finds and ranks jobs, helps with application forms and tracks where each application stands. Every fact in a CV must be found in your documents: a script checks it, and a
CV that fails is not saved.

It is one [Agent Skill](https://agentskills.io): a `SKILL.md`, reference files and Python scripts that use the
standard library only. The agent (Claude, Codex) reads, judges and writes; the scripts do what must not be left
to a model.

| The agent does | The scripts do |
|---|---|
| Reads your documents and drafts the profile | Keep only facts found in the documents, and list what was left out |
| Reads a posting and writes the CV | Check every employer, title, date, number and skill against the profile; render the PDF |
| Rates a job on five criteria | Fetch job boards, drop duplicates and what your preferences rule out, compute the score |
| Fills a form with its browser, stops before submit | Give the address, the CV file, your details and remembered answers |
| Reads a reply email, records the outcome | Statuses only move forward |

## Install

Get this `jobpilot` folder. For an upload, zip it from the folder above (the tests are
not needed): `zip -r jobpilot.zip jobpilot -x 'jobpilot/tests/*' -x '*/__pycache__/*'`.

- **Claude Desktop and claude.ai**: Settings > Capabilities > Skills, upload `jobpilot.zip`. Code execution
  must be on.
- **Claude Code**: copy the folder to `~/.claude/skills/jobpilot` (or `.claude/skills/jobpilot` in a project).
- **Codex**: copy the folder to `~/.agents/skills/jobpilot` (or `.agents/skills/jobpilot` in a repository).

Needs Python 3.9 or newer. For a PDF it needs Typst: `pip install typst`, or the
[typst program](https://typst.app/open-source/). Without Typst the CV is a print-ready HTML file.

## Use

Talk to the agent:

- "Here is my CV and my LinkedIn export. Set up my profile."
- "Tailor my CV for this job: https://..."
- "I want senior backend roles, remote, in Europe. Find jobs at these companies: ..."
- "Help me fill the application form for the Acme job."
- "Where do my applications stand? I got an email from Acme, here it is."

What to expect:

- After reading your documents, it tells you what it could **not** trace to them. To add a fact, add it to a
  document; nothing is typed into the profile directly.
- After a CV, it tells you what the CV leads with, what it left out, and the gaps: requirements of the job
  that your documents show no evidence for.
- It never submits an application without your yes for that job, and it never solves a login or a captcha:
  it stops and hands over to you.

## What differs between hosts

| | Claude Code, Codex, Claude Desktop | claude.ai (web) |
|---|---|---|
| Data between sessions | kept in `~/.jobpilot` (or `JOBPILOT_DATA_DIR`) | lost with the sandbox: the skill exports `jobpilot-data.zip` for you to keep and attach next time |
| Reading job boards and links | from the scripts | only if the sandbox has internet; otherwise the agent uses its own web tool, or you paste the posting |
| Reply emails | a Gmail connector if the host has one (read-only), else you paste the email | same |
| Application forms | a browser tool if the host has one (stops before submit), else you submit | you submit |

Tested here: the scripts on Linux with Python 3.11 and 3.13, and the skill loading in Claude Code. Not yet
tested: Claude Desktop, claude.ai, Codex, Windows, and Python 3.9 and 3.10 (the code is checked to parse as
3.9).

## Job sources

Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Personio and Workday boards. Any other page (a
company careers page, Hacker News, YC) the agent reads with its own web tool and adds as a single job. A page
that needs a browser or a login is reported, not worked around.

## Privacy and safety

- Your documents, profile, CVs and answers stay in the data folder on the machine the agent runs on. The
  scripts send nothing anywhere; they only read public job pages, with a named user agent, one request per
  host per second.
- The scripts refuse addresses on your machine or a private network.
- Text from postings, pages and emails is treated as data: the agent is told never to follow
  instructions inside it, and the scripts hand it over between markers.
- No passwords, keys or tokens are stored or asked for.

## Develop

From this folder:

```sh
python3 -m unittest discover -s tests -t .   # the tests and the skill's format (no network needed)
```

`AGENTS.md` describes the layout and the rules, `REVIEW.md` what to look for in a change. The skill's history,
and the desktop app it replaced (Rust, Tauri, a Chrome extension), are in the `phucvinh57/jobpilot` repository.

## Licence

MIT. The CV template uses New Computer Modern, which ships with Typst.
