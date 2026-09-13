#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python_cmd=""
  for candidate in "${MAILWARD_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      python_cmd="$candidate"
      break
    fi
  done
  if [ -z "$python_cmd" ]; then
    echo "Python 3.11 or newer is required. Install it, then rerun ./run.sh." >&2
    exit 2
  fi
  "$python_cmd" -m venv .venv
fi
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Existing .venv uses Python older than 3.11. Move that directory aside and rerun ./run.sh." >&2
  exit 2
fi
if ! .venv/bin/python -c 'import google_auth_oauthlib, googleapiclient, google.auth' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements-gmail.lock.txt
fi
exec .venv/bin/python -m mail_agent.web --db data/web-groq.sqlite3 "$@"
