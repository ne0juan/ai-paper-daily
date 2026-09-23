#!/usr/bin/env bash
# Publish a directory to the `reports` branch under <name>/ (kept out of main history).
# Usage: scripts/push_report.sh <src_dir> <name>
set -euo pipefail
SRC=$(realpath "$1"); NAME=$2
WT=$(mktemp -d)
git config user.name "paper-radar-bot"; git config user.email "paper-radar-bot@users.noreply.github.com"
if git ls-remote --exit-code --heads origin reports >/dev/null 2>&1; then
  git fetch -q --depth 1 origin reports
  git worktree add -q "$WT" FETCH_HEAD
  (cd "$WT" && git checkout -q -B reports)
else
  git worktree add -q --detach "$WT"
  (cd "$WT" && git checkout -q --orphan reports && git rm -rqf . >/dev/null 2>&1 || true)
fi
rm -rf "$WT/$NAME"; mkdir -p "$WT/$NAME"; cp -r "$SRC"/. "$WT/$NAME/"
cd "$WT"
git add -A
git commit -qm "report: $NAME $(date -u +%FT%TZ)" || { echo "no changes"; exit 0; }
for i in 1 2 3; do git push -q origin HEAD:reports && exit 0; sleep 3; git pull -q --rebase origin reports || true; done
exit 1
