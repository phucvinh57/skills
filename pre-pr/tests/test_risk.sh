#!/usr/bin/env bash
# Tests for scripts/risk.sh and templates/check.sh in throwaway git repos. Usage: bash tests/test_risk.sh
set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
risk="$here/scripts/risk.sh"
check="$here/templates/check.sh"
fails=0
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

expect() { # name, expected substring, actual output
  if [[ $3 == *"$2"* ]]; then echo "ok   $1"; else
    echo "FAIL $1: expected '$2' in:"
    sed 's/^/     /' <<<"$3"
    fails=$((fails + 1))
  fi
}
expect_rc() { # name, expected code, actual code
  if [[ $2 == "$3" ]]; then echo "ok   $1"; else
    echo "FAIL $1: exit $3, expected $2"
    fails=$((fails + 1))
  fi
}

new_repo() {
  local d="$tmp/$1"
  git init -q -b main "$d"
  git -C "$d" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  git -C "$d" switch -q -c work
  echo "$d"
}

r=$(new_repo empty)
expect "no changes" "No changed paths" "$(cd "$r" && "$risk")"

r=$(new_repo low)
mkdir -p "$r/docs" && echo x >"$r/docs/a.md" && echo x >"$r/README.md"
expect "docs are low" "risk: low" "$(cd "$r" && "$risk")"

r=$(new_repo medium)
mkdir -p "$r/src" && echo x >"$r/src/a.ts" && echo x >"$r/README.md"
expect "code is medium" "risk: medium" "$(cd "$r" && "$risk")"
out=$(cd "$r" && "$risk" --label low)
rc=$?
expect "label below tier fails" "FAILED" "$out"
expect_rc "label below tier exit code" 1 $rc
(cd "$r" && "$risk" --label high >/dev/null)
expect_rc "label above tier passes" 0 $?

r=$(new_repo high)
mkdir -p "$r/.github" && echo x >"$r/.github/ci.yml" && mkdir -p "$r/src" && echo x >"$r/src/a.ts"
out=$(cd "$r" && "$risk")
expect "default high path" "risk: high" "$out"
expect "lists high path" "  .github/ci.yml" "$out"

r=$(new_repo conf)
mkdir -p "$r/scripts" "$r/src/auth" && printf '%s\n' '# comment' 'high ^src/auth/' 'low ^src/docs/  # trailing' >"$r/scripts/risk.conf"
git -C "$r" add -A && git -C "$r" -c user.email=t@t -c user.name=t commit -q -m conf
git -C "$r" switch -q main && git -C "$r" merge -q work && git -C "$r" switch -q -c work2
echo x >"$r/src/auth/login.ts"
expect "config high rule" "risk: high" "$(cd "$r" && "$risk")"
rm "$r/src/auth/login.ts" && mkdir -p "$r/src/docs" && echo x >"$r/src/docs/a.md"
expect "config low rule" "risk: low" "$(cd "$r" && "$risk")"
echo x >"$r/.github-like.txt"
expect "config replaces defaults" "risk: medium" "$(cd "$r" && "$risk")"
rm "$r/.github-like.txt"
echo "bogus ^x" >"$r/scripts/risk.conf"
out=$(cd "$r" && "$risk")
rc=$?
expect "bad tier rejected" "unknown tier" "$out"
expect_rc "bad tier exit code" 2 $rc

r=$(new_repo checks)
mkdir -p "$r/scripts" && cp "$check" "$r/scripts/check.sh"
git -C "$r" add -A && git -C "$r" -c user.email=t@t -c user.name=t commit -q -m check
git -C "$r" switch -q main && git -C "$r" merge -q work && git -C "$r" switch -q -c work2
expect "check: nothing changed" "No checks cover" "$(cd "$r" && scripts/check.sh)"
sed -i 's/^check_web() {/check_web() { run web-true true; return; /' "$r/scripts/check.sh"
git -C "$r" -c user.email=t@t -c user.name=t commit -qam edit
git -C "$r" switch -q main && git -C "$r" merge -q work2 && git -C "$r" switch -q -c work3
mkdir -p "$r/src" && echo x >"$r/src/a.ts"
expect "check: web group runs" "PASSED: web-true" "$(cd "$r" && scripts/check.sh --only web)"

echo
if ((fails)); then
  echo "$fails failed"
  exit 1
fi
echo "all passed"
