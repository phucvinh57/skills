# Review guide

For human reviewers and agents reviewing a change. When a bug slips through, add a line under **Misses**.

## Failures

- **A CV states a fact that is not in the user's documents** (the profile read from them): an employer, title, date, number, technology or achievement. Important.
- **The profile gets a fact no document states:** `trace.py` lets an invented fact through, or code writes one. Important.
- **A check got weaker:** `guard.py`, `trace.py` or `trends/cite.py` accepts what it refused, without a test that says why. Important.
- **Untrusted text reaches the agent without markers,** or a reference file tells the agent to act on it. Important.
- **A request leaves `jp.http`,** or reaches a private or local address. Important.
- **Something is submitted, or a status moves backwards, without the user's word.**
- **Contact details or links changed** (email, phone, location, GitHub, LinkedIn, website).
- **The CV renders wrong:** unescaped markup, a broken link, text cut off, over 2 pages (the target is 1).
- **A reference file and its command disagree** (a renamed flag, a changed output key).
- **Docs out of date** with the change (README, AGENTS.md).

## Risk tiers

Set by the paths touched. The highest wins. Paths are relative to the `jobpilot/` folder. There is no CI: `python3 -m unittest discover -s tests -t .` is the gate.

- **High** (a human reads the diff): `SKILL.md`, `references/`, `assets/`, `scripts/jp/{trace,guard,http,state}.py`, `scripts/jp/trends/cite.py`, `tests/validate_skill.py`, `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`.
- **Low:** `README.md`, other docs.
- **Medium:** everything else.

## Ignore

`__pycache__/` and generated PDFs.

## Reporting

- Start with the number of findings, or "No issues found."
- Cite `path:line` for each. Never guess file names.
- Label each Important, Nit, or Pre-existing.
- For a bug fix, name the root cause. Name the class of bug so every instance gets fixed.

## Rounds

At most 3 per PR; after that, question the approach. After round 1, review only new commits. Don't re-raise anything already fixed or answered.

## Misses

One line per bug that shipped: PR, what broke, which check should have caught it.
