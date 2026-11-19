#!/usr/bin/env bash
# Publish docs/wiki/ (and docs/images/) to the repository's GitHub wiki.
# The wiki must exist first: on GitHub, open the Wiki tab and save any first page.
#   docs/wiki/publish.sh [owner/repo]
set -euo pipefail
origin="$(git -C "$(dirname "$0")" remote get-url origin)"
origin="${origin#git@github.com:}"; origin="${origin#https://github.com/}"
repo="${1:-${origin%.git}}"
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
