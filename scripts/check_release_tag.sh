#!/usr/bin/env bash
# Fails unless the release tag is vX.Y.Z, the run is on that tag (RUN_REF), and
# the tag still points at the commit the run started on (GITHUB_SHA), on main.
# Both the release and the MCP Registry workflows run it right after checkout.
# Usage: check_release_tag.sh <tag> <workflow file>
set -euo pipefail
tag="$1"
workflow="$2"
# Plain releases only: no pre-release suffixes and no leading zeros, so the tag,
# __version__ and the built file names are the same string.
if ! [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "::error::'$tag' is not a release tag (vX.Y.Z)"
  exit 1
fi
if [ "$RUN_REF" != "refs/tags/$tag" ]; then
  echo "::error::run this on the tag: gh workflow run $workflow --ref $tag -f tag=$tag (ran on $RUN_REF)"
  exit 1
fi
# Full ref names, so a tag named like a branch can't stand in for main.
tagged=$(git rev-parse "refs/tags/${tag}^{commit}")
[ "$tagged" = "$GITHUB_SHA" ] \
  || { echo "::error::$tag now points at $tagged, not $GITHUB_SHA the run started on"; exit 1; }
git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main \
  || { echo "::error::$tag is not on main"; exit 1; }
echo "$tag is this commit, on main"
