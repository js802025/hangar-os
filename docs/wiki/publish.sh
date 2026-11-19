#!/usr/bin/env bash
# Publish docs/wiki/ (and docs/images/) to the repository's GitHub wiki.
# The wiki must exist first: on GitHub, open the Wiki tab and save any first page.
#   docs/wiki/publish.sh [owner/repo]
set -euo pipefail
repo="${1:-$(git remote get-url origin | sed -E "s#(git@github.com:|https://github.com/)##; s#\.git$##")}"
here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --quiet "https://github.com/${repo}.wiki.git" "$tmp/wiki"
find "$tmp/wiki" -maxdepth 1 -name '*.md' -delete
cp "$here"/*.md "$tmp/wiki/"
rm -rf "$tmp/wiki/images" && mkdir "$tmp/wiki/images" && cp "$here"/../images/*.png "$tmp/wiki/images/"
cd "$tmp/wiki"
git add -A
if git diff --cached --quiet; then echo "wiki already up to date"; exit 0; fi
git commit --quiet -m "Update the wiki from docs/wiki"
git push --quiet
echo "published https://github.com/${repo}/wiki"
