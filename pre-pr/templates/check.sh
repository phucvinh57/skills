#!/usr/bin/env bash
# Runs the checks that cover files changed since the merge base with the default branch (committed
# and uncommitted). Checks come in independent groups, each run only when its paths changed.
# Edit the GROUPS section for this project. Run by the pre-push hook and `/pre-pr`.
#
# Usage: check.sh [--all] [--only <group>]... [base-ref]
#   --all     every group, whatever changed
#   --only    run only these groups (still filtered by the changed paths unless --all)
set -uo pipefail

# ---- GROUPS (edit) ---------------------------------------------------------------------------
# Each group: a path regex (ERE, repo-relative) and a function that runs its checks with `run`.
# A change to this script runs every group.
self='scripts/check\.sh$'
groups=(web rust)
declare -A paths=(
  [web]="^(src/|package\.json$|bun\.lock$|package-lock\.json$|tsconfig\.json$|$self)"
  [rust]="^(Cargo\.(toml|lock)$|crates/|src-tauri/|$self)"
)
check_web() {
  run web-install npm ci &&
    run web-lint npm run lint &&
    run web-test npm test &&
    run web-build npm run build
}
check_rust() {
  run clippy cargo clippy --workspace --all-targets -- -D warnings &&
    run test cargo test --workspace
}
# ---- end of GROUPS ---------------------------------------------------------------------------

all=false
only=()
while (($#)); do
  case "$1" in
    --all) all=true ;;
    --only)
      [[ " ${groups[*]} " == *" ${2:-} "* ]] || {
        echo "--only takes one of: ${groups[*]}"
        exit 2
      }
      only+=("$2")
      shift
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
  echo main
}

changed=""
if ! $all; then
  if [[ -n ${1:-} ]]; then
    base="$1"
  else
    db=$(default_branch)
    if git rev-parse -q --verify "origin/$db" >/dev/null; then base="origin/$db"; else base="$db"; fi
  fi
  if ! mb=$(git merge-base "$base" HEAD 2>/dev/null); then
    echo "No merge base with $base. Pass a base ref or use --all."
    exit 1
  fi
  changed=$({
    git diff --name-only "$mb"
    git ls-files --others --exclude-standard
  } | sort -u)
fi

wanted() {
  local group="$1"
  if ((${#only[@]} > 0)) && [[ " ${only[*]} " != *" $group "* ]]; then
    return 1
  fi
  $all || grep -qE "${paths[$group]}" <<<"$changed"
}

failed=()
ran=()
run() {
  local name="$1"
  shift
  ran+=("$name")
  echo "::: $name: $*"
  if ! "$@"; then
    failed+=("$name")
    return 1
  fi
}

for g in "${groups[@]}"; do
  wanted "$g" && "check_$g"
done

echo
if ((${#ran[@]} == 0)); then
  echo "No checks cover the changed paths."
  exit 0
fi
if ((${#failed[@]} > 0)); then
  echo "FAILED: ${failed[*]} (ran: ${ran[*]})"
  exit 1
fi
echo "PASSED: ${ran[*]}"
