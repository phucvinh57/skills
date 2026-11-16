---
name: pre-pr
description: Take finished work to open pull requests — run the checks for what changed, review the diff against docs/REVIEW.md and fix findings, commit, open the PR (or a gh stack of PRs when the changes are related), re-check the final commit locally and merge Low and Medium PRs. Use when the user runs /pre-pr, asks to open/ship a PR, or when work in a worktree is done.
---

# Pre-PR

This skill is the review and the checks before a merge: its local runs of the project's check script and of `risk.sh` are the gate, whether or not the repo also has CI. Report evidence, not claims.

`<skill-dir>` below is the directory holding this file. `<default>` is the repo's default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`, else `main`).

## 0. Project files

The skill reads these from the repo, and falls back when one is missing:

| File | Purpose | Fallback |
| --- | --- | --- |
| `docs/REVIEW.md` | what to catch, risk tiers, merge policy, reporting | `<skill-dir>/templates/REVIEW.md` |
| `scripts/check.sh` | the checks for the changed paths | the checks the project documents (README, AGENTS.md, CLAUDE.md, package scripts, CI config) |
| `scripts/risk.sh` | the risk tier the changed paths require | `<skill-dir>/scripts/risk.sh` (reads `scripts/risk.conf`, else built-in defaults) |
| `.github/pull_request_template.md` | PR body headings | `<skill-dir>/templates/pull_request_template.md` |

If any is missing, say so once and offer to add them from `<skill-dir>/templates/` (and `scripts/risk.sh` from `<skill-dir>/scripts/`) as their own commit, adapting `check.sh`'s GROUPS and `risk.conf` to the project. Don't add them without the user's yes; carry on with the fallbacks meanwhile.

Wherever the project's own docs (AGENTS.md, CLAUDE.md, `docs/REVIEW.md`) set a different policy — branch names, which tiers merge, docs-only pushes — theirs wins.

## 1. Scope

- **Docs-only**, if `docs/REVIEW.md` allows it (every changed path is under `docs/`, and none is `docs/REVIEW.md`): commit on `<default>`, `git push origin <default>`, report the commit and stop. No PR, no checks.
- Otherwise never work on `<default>`. If the change isn't on its own branch (a worktree is best, e.g. under `.claude/worktrees/`), move it onto one.
- `git fetch origin <default>`, read `git status --short` and `git diff origin/<default>...HEAD --stat`, then run `risk.sh`: it prints the tier the changed paths require and lists the High paths. Use that tier, or a higher one if the change warrants it.
- Choose a shape:
  - **One PR** for one coherent change.
  - **A `gh stack`** when the layers build on each other and each can be reviewed and pass checks alone (e.g. a core API → the view that uses it), and the `gh stack` extension is installed.
  - **Separate branches** for unrelated changes.

## 2. Verify

- Run `scripts/check.sh` (or the fallback checks), fix failures and keep its output for the PR body.
- For a UI change, check it visually the way the project documents (AGENTS.md / CLAUDE.md), or with a browser screenshot if it documents none.

## 3. Review

Review with fresh context against `docs/REVIEW.md`. In a stack, review each layer against its parent.

- If the host has a `code-review` skill, run it at the tier's level (`low` / `medium` / `high`); for the High tier also run `security-review` if available. Otherwise review the diff in a fresh subagent (or a fresh pass) against `docs/REVIEW.md`, giving it the diff, not your conclusions.
- Check every **Failures** item in `docs/REVIEW.md` that the change could touch.
- Fix every Important finding. Fix each other finding, or give a one-line reason not to.
- Repeat steps 2–3 on the new changes only, up to 3 rounds. If findings persist after that, question the approach with the user.
- If the same kind of finding recurs, propose a fix at the source: an AGENTS.md/CLAUDE.md rule, a `REVIEW.md` line or a check in `scripts/check.sh`.

## 4. Commit and open

- Branch names are `claude/<topic>` (or the host's assigned branch), and stack layers are `claude/<topic>-<layer>`.
- Commit messages are `fix(scope): …` for fixes and a plain imperative sentence for features, unless the repo uses another convention (read `git log`).
- Never use `--no-verify`: a pre-push hook may re-run `check.sh`.
- Write each PR body to a scratch file following the PR template:
  - **What changed:** the content, not the file list.
  - **Testing:** the commands actually run and their results.
  - **Risk:** the tier and what could break.
  - **How to revert:** `git revert` is enough unless data was removed. Then say what to restore.
  - Any findings left open, with their reasons.
- Create the `risk: low|medium|high` labels if the repo lacks them (`gh label create "risk: high"`).

Single PR (with the GitHub CLI; use the host's GitHub tools if `gh` is unavailable):

```sh
git switch -c claude/<topic>
git add -A && git commit -m "<message>"
git push -u origin HEAD
gh pr create --base <default> --title "<title>" --body-file <body.md> --label "risk: <tier>"
# existing PR: gh pr edit --body-file <body.md> --add-label "risk: <tier>"
```

Stack (bottom-up from `<default>`):

```sh
gh stack init claude/<topic>-<layer1>
git add <files> && git commit -m "<message>"
gh stack add -Am "<message>" claude/<topic>-<layer2>
gh stack submit --auto --open
gh pr edit <branch> --title "<title>" --body-file <body.md> --add-label "risk: <tier>"
```

To fix a layer: `gh stack checkout <branch>`, commit, `gh stack rebase --upstack`, then `gh stack push`.

## 5. Final check

Check the exact commit that will merge, on each PR's branch (in a stack, each layer):

1. `git fetch origin <default>` and make sure the branch contains the latest `origin/<default>` (merge it in if not, then push).
2. Run the checks and `risk.sh --label <tier>` on the pushed head, with a clean `git status`, where `<tier>` is read from the PR itself: `gh pr view <pr> --json labels` must show exactly one `risk: ` label. Keep both outputs for the report (and the PR body if they changed since). If the repo has CI, its checks on that commit must pass too.
3. On a failure, fix on the branch, push, and repeat from step 1. Stop and ask after 3 failed rounds, or when a failure is flaky or comes from the environment (missing tool, network).

If a failure isn't caused by the PR (it fails on `<default>` too), fix it separately and merge `<default>` in.

## 6. Merge

Each PR carries exactly one label, `risk: low`, `risk: medium` or `risk: high`, matching its tier. Label each stack layer by its own diff (`risk.sh <parent branch>`). `risk.sh --label <tier>` passing is the check.

Unless `docs/REVIEW.md` says otherwise:

- **Low or Medium:** once step 5 passes on the PR's head commit, run `gh pr merge <pr> --merge --delete-branch`.
- **High:** never merge. A human reads the diff and merges.
- **Stack:** merge only a PR whose base is `<default>`. Then run `gh stack sync --prune`, and continue with the next layer: run step 5 on it, then merge it if its tier allows.
- If a fix raises the tier to High, run `gh pr edit <pr> --remove-label "risk: <old>" --add-label "risk: high"` and don't merge.
- If the user or the host's instructions forbid merging, stop after step 5 and say the PR is ready.

## 7. Report

List each PR URL with its tier, the final check and `risk.sh` results, and whether it was merged or waits for a human.
