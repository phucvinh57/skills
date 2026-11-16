#!/usr/bin/env bash
# Prints the risk tier (low, medium or high) that the paths changed since the merge base with the
# default branch require: the highest of their tiers. With --label, fails when that label is lower
# than the required tier. `/pre-pr` runs it to pick the PR's label and again before merging.
#
# Tiers come from a config file, the first found of: $RISK_CONF, scripts/risk.conf, .github/risk.conf.
# One rule per line, `high <ERE>` or `low <ERE>`, matched against repo-relative paths; `#` starts a
# comment. A path that matches no rule is medium. Without a config the defaults below apply.
#
# Usage: risk.sh [--label low|medium|high] [base-ref]
#   base-ref defaults to origin/<default branch>, else the local default branch.
set -uo pipefail

label=""
while (($#)); do
  case "$1" in
    --label)
      [[ "${2:-}" =~ ^(low|medium|high)$ ]] || {
        echo "--label takes low, medium or high"
        exit 2
      }
      label="$2"
      shift
      ;;
    -h | --help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*)
      echo "Unknown option $1"
      exit 2
      ;;
    *) break ;;
  esac
  shift
done
root=$(git rev-parse --show-toplevel) || exit 1
cd "$root" || exit 1

default_branch() {
  local ref
  ref=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null) && {
    echo "${ref#origin/}"
    return
  }
  for b in main master; do
    git rev-parse -q --verify "$b" >/dev/null || git rev-parse -q --verify "origin/$b" >/dev/null && {
      echo "$b"
      return
    }
  done
  echo main
}

# origin/<default> first: a local default branch is often stale and would count other merged work.
if [[ -n ${1:-} ]]; then
  base="$1"
else
  db=$(default_branch)
  if git rev-parse -q --verify "origin/$db" >/dev/null; then base="origin/$db"; else base="$db"; fi
fi
if ! mb=$(git merge-base "$base" HEAD 2>/dev/null); then
  echo "No merge base with $base. Pass a base ref."
  exit 1
fi

# Defaults: the agent's own instructions, the gate scripts, CI and the review guide are High;
# README and docs are Low; everything else is Medium.
high=('^(\.github/|\.claude/|\.agents/|\.githooks/|scripts/|AGENTS\.md$|CLAUDE\.md$|docs/REVIEW\.md$|(\.config/)?\.?lefthook(-local)?\.(ya?ml|jsonc?|toml)$)')
low=('^(README\.md$|docs/)')

conf=""
for c in "${RISK_CONF:-}" scripts/risk.conf .github/risk.conf; do
  [[ -n $c && -f $c ]] && {
    conf=$c
    break
  }
done
if [[ -n $conf ]]; then
  high=()
  low=()
  n=0
  while IFS= read -r line || [[ -n $line ]]; do
    n=$((n + 1))
    line="${line%%#*}"
    read -r tier re <<<"$line"
    [[ -z ${tier:-} ]] && continue
    if [[ -z ${re:-} ]]; then
      echo "$conf:$n: a rule is \`high <ERE>\` or \`low <ERE>\`"
      exit 2
    fi
    case "$tier" in
      high) high+=("$re") ;;
      low) low+=("$re") ;;
      *)
        echo "$conf:$n: unknown tier '$tier' (high or low)"
        exit 2
        ;;
    esac
  done <"$conf"
fi
join() {
  local IFS='|'
  echo "$*"
}
# A pattern that never matches, for an empty list.
high_re=$(join "${high[@]}")
low_re=$(join "${low[@]}")
high_re=${high_re:-'^$x'}
low_re=${low_re:-'^$x'}

rank() { case "$1" in low) echo 1 ;; medium) echo 2 ;; high) echo 3 ;; *) echo 0 ;; esac }

# Renames count under both names.
changed=$({
  git diff --name-only --no-renames "$mb"
  git ls-files --others --exclude-standard
} | sort -u)

if [[ -z $changed ]]; then
  echo "No changed paths since $base."
  exit 0
fi

high_hits=$(grep -E "$high_re" <<<"$changed" || true)
medium_hits=$(grep -vE "($high_re)|($low_re)" <<<"$changed" || true)
if [[ -n $high_hits ]]; then
  tier=high
elif [[ -n $medium_hits ]]; then
  tier=medium
else
  tier=low
fi

echo "risk: $tier"
if [[ -n $high_hits ]]; then
  echo "High paths:"
  sed 's/^/  /' <<<"$high_hits"
fi

if [[ -n $label ]] && (($(rank "$label") < $(rank "$tier"))); then
  echo "FAILED: labelled risk: $label, but these paths need risk: $tier."
  exit 1
fi
