#!/usr/bin/env bash
# Fails if a tracked file holds a provider key or a credential-shaped literal, or .env.example ships a value.
# Two nets: known vendor key prefixes, and any *_API_KEY / *_TOKEN assigned a long literal.
set -u
found=0

if git grep -nIE '(^|[^A-Za-z0-9])(tvly-|ydc-|sk-)[A-Za-z0-9_-]{16,}' -- . ':!.github/workflows/*' ':!scripts/check_credentials.sh'; then
  echo "::error::A provider API key appears in a tracked file."
  found=1
fi

# Constants naming a header, env var or prefix are not credentials --
# BRAVE_API_KEY_HEADER = "X-Subscription-Token" is a field name.
if git grep -nIE '[A-Z_]*(API_KEY|TOKEN|SECRET)[A-Z_]*[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9_./+-]{16,}' \
     -- . ':!.github/workflows/*' ':!scripts/check_credentials.sh' ':!tests/*' ':!*.md' \
   | grep -vE '^[^:]+:[0-9]+:(ENV_|[A-Z_]*_(HEADER|PREFIX|NAME|ENV|VAR)[[:space:]]*=)'; then
  echo "::error::A credential-shaped literal appears in a tracked file."
  found=1
fi

if grep -qE '^[A-Z_]+=.+' .env.example; then
  echo "::error::.env.example must ship empty values."
  found=1
fi

exit $found
