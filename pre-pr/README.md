# Pre-PR

A portable Agent Skill that takes finished work to a merged (or human-reviewed) pull request: run the checks for what changed, review the diff against the project's review guide and fix findings, commit, open the PR (or a `gh stack` of PRs), re-check the exact commit that will merge, and merge Low and Medium risk PRs. High risk PRs wait for a human.

It suits repos where an agent ships its own PRs and local checks are the gate, with or without CI.

## Install

Copy the entire `pre-pr` folder into either location:

| Host | Project installation | Personal installation |
| --- | --- | --- |
| Claude Code | `.claude/skills/pre-pr/` | `~/.claude/skills/pre-pr/` |
| Codex | `.agents/skills/pre-pr/` | `~/.agents/skills/pre-pr/` |

To serve both hosts from one copy, put it in `.agents/skills/pre-pr/` and symlink `.claude/skills/pre-pr -> ../../.agents/skills/pre-pr`.

## Dependencies

- `git` and `bash` 4+.
- The [GitHub CLI](https://cli.github.com/) (`gh`), logged in, to open, label and merge PRs. Optional: the `gh stack` extension for stacked PRs.

## Set up a project

The skill works with nothing added, but it is meant to read four files from the repo. Ask the agent to "set up pre-pr" and it offers to add them from this folder, or copy them yourself:

| Copy | To | Then |
| --- | --- | --- |
| `templates/REVIEW.md` | `docs/REVIEW.md` | List what reviewers must catch in this project, and the risk tiers. |
| `scripts/risk.sh` | `scripts/risk.sh` | Nothing; it reads `scripts/risk.conf`. |
| `templates/risk.conf` | `scripts/risk.conf` | Add the project's sensitive paths as `high` rules. |
| `templates/check.sh` | `scripts/check.sh` | Edit the GROUPS section: which paths trigger which checks. |
| `templates/pull_request_template.md` | `.github/pull_request_template.md` | Optional. |

Run `scripts/check.sh` from a pre-push hook (e.g. [lefthook](https://lefthook.dev)) so a push without the checks is impossible.

### `risk.sh`

Prints `risk: low|medium|high` for the paths changed since the merge base with the default branch, and the High paths. With `--label <tier>` it fails when the label is below the required tier.

```sh
scripts/risk.sh                 # tier for this branch vs origin/<default>
scripts/risk.sh --label medium  # fails if the change needs high
scripts/risk.sh feature-base    # compare against another base
```

Rules: `high <ERE>` / `low <ERE>`, one per line, from `$RISK_CONF`, `scripts/risk.conf` or `.github/risk.conf`. A path matching no rule is medium; the highest tier wins.

## Use

In Claude Code invoke `/pre-pr`; in Codex invoke `$pre-pr`. Or just say "ship this" / "open a PR" when the work is done.

## Verify the helpers

```sh
bash /path/to/pre-pr/tests/test_risk.sh
```
