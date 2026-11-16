# Review guide

For human reviewers and the `/pre-pr` skill. When a bug slips through, add a line under **Misses**.

## Failures

What a reviewer must catch in this project, most severe first. Mark the ones that block a merge
**Important**. Replace these examples with the project's own.

- **A secret reaches a log, an error message, the repo or a client.** Important.
- **Untrusted input is executed or rendered as markup** (SQL, shell, HTML). Important.
- **A schema migration edits an existing one** instead of appending. Important.
- **Docs out of date** with the change (README, AGENTS.md, skills).

## Risk tiers

Set by the paths touched; the highest wins. Each PR gets exactly one label: `risk: low`,
`risk: medium` or `risk: high`. Low and Medium PRs are merged by `/pre-pr` once `scripts/check.sh`
passes on the PR's final commit. High PRs wait for a human. `scripts/risk.sh --label <tier>` checks
the label against `scripts/risk.conf`: change the list there along with this one.

- **High** (a human reads the diff): `scripts/`, `.github/`, `.claude/`, `.agents/`, `AGENTS.md`,
  `CLAUDE.md`, `docs/REVIEW.md`, git hook config.
- **Low:** `README.md`, other docs.
- **No PR:** a change that touches only `docs/` (except this file) is pushed directly to the default
  branch. Delete this line to require a PR for every change.
- **Medium:** everything else.

## Ignore

Build output and generated files (e.g. `target/`, `dist/`, `node_modules/`).

## Reporting

- Start with the number of findings, or "No issues found."
- Cite `path:line` for each. Never guess file names.
- Label each Important, Nit, or Pre-existing.
- For a bug fix, name the root cause. Name the class of bug so every instance gets fixed.

## Rounds

At most 3 per PR; after that, question the approach. After round 1, review only new commits. Don't
re-raise anything already fixed or answered.

## Misses

One line per bug that shipped: PR, what broke, which check should have caught it.
