#!/usr/bin/env bash
# Prints a 12-char id of the backend code in the working tree: every lambdas/flight-*/
# folder plus infra/, committed or not. Same code -> same id, wherever it runs.
#
# infra/deploy.sh stamps it on the stack (BackendVersion -> GET /version), and the Vite dev
# server serves it at /__backend_version, so the dev frontend can tell when the deployed
# backend doesn't match your checkout (src/components/BackendVersionBanner.tsx).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export GIT_INDEX_FILE="$tmp/index" # a scratch index: your real staging area is untouched

git read-tree HEAD
git add -A -- lambdas infra
tree=$(git write-tree)
git ls-tree "$tree" -- infra lambdas/ \
  | grep -E $'\t(infra|lambdas/flight-[^/]+)$' \
  | git hash-object --stdin \
  | cut -c1-12
