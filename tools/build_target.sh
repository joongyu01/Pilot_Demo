#!/usr/bin/env bash
# Build one demo target from the tip of an upstream branch, check it, print the commit.
#   tools/build_target.sh <repo> <branch> <site dir> <url prefix>...
# Each URL prefix gets one browser run (the root demo is tested at /Pilot_Demo/ and /).
# Logs go to stderr; stdout is only the commit, for the workflow to record.
set -euo pipefail
repo=$1 branch=$2 out=$3
shift 3
src=$(mktemp -d)

git clone --quiet --filter=blob:none --no-checkout --depth 1 --branch "$branch" "https://github.com/${repo}.git" "$src"
git -C "$src" sparse-checkout set --no-cone \
  '/LICENSE' \
  '/openpilot/__init__.py' \
  '/openpilot/common/' \
  '/openpilot/selfdrive/__init__.py' \
  '/openpilot/selfdrive/carrot/' \
  '/openpilot/selfdrive/carrot_settings.json' \
  '/openpilot/selfdrive/ui/translations/' \
  '/openpilot/selfdrive/assets/' \
  '!/openpilot/selfdrive/assets/fonts/' \
  '!/openpilot/selfdrive/assets/training/' \
  '/opendbc_repo/opendbc/'
git -C "$src" checkout --quiet
sha=$(git -C "$src" rev-parse HEAD)
echo "::notice::${branch} ${sha:0:8}: $(git -C "$src" log -1 --format=%s)" >&2

(cd "$src/openpilot/selfdrive/carrot/web" && npm ci --no-audit --no-fund && npm run build) >&2
UPSTREAM_REPO="$repo" UPSTREAM_BRANCH="$branch" UPSTREAM_SHA="$sha" \
  UPSTREAM_DATE="$(git -C "$src" log -1 --format=%cI)" UPSTREAM_SUBJECT="$(git -C "$src" log -1 --format=%s)" \
  python tools/build_site.py --upstream "$src" --out "$out" >&2
python tools/smoke_backend.py "$out/_demo/backend.zip" >&2
for prefix in "$@"; do
  DEMO_TEST_PREFIX="$prefix" node tests/e2e.mjs "$out" >&2
done
echo "$sha"
