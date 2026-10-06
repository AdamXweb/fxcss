#!/usr/bin/env bash
# Serves the built Worker under wrangler (workerd, the production runtime) and
# runs the production checks against it. Website CI and the Refresh docs
# workflow both run this after `npm run build`; locally, `just preview` and
# `just verify` do the same in two terminals.
set -euo pipefail

port=4318
log="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/website.log"

npm start -- --ip 127.0.0.1 --port "$port" --log-level error >"$log" 2>&1 &
preview_pid=$!
trap 'kill "$preview_pid" 2>/dev/null || true' EXIT

for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:$port/" >/dev/null; then
    node scripts/check-site.mjs "http://127.0.0.1:$port"
    exit 0
  fi
  sleep 1
done
cat "$log"
echo "The built site did not answer on port $port within 30 seconds." >&2
exit 1
