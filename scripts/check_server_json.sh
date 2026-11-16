#!/usr/bin/env bash
# Fails unless server.json names the release tag's version (vX.Y.Z → X.Y.Z) in its three places.
# The release workflow runs it before PyPI and the MCP Registry workflow before it publishes.
set -euo pipefail
version="${1#v}"
jq -e --arg v "$version" '
  .version == $v
  and .packages[0].version == $v
  and .packages[0].runtimeArguments[0].value == "webrouter[mcp]==\($v)"
' server.json >/dev/null || {
  echo "::error::server.json does not name $version in all three places"
  exit 1
}
echo "server.json names $version"
