#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Create the environment once: python3 -m venv .venv && .venv/bin/pip install -r requirements-gmail.txt"
  exit 2
fi
if ! .venv/bin/python -c 'import google_auth_oauthlib, googleapiclient, google.auth' 2>/dev/null; then
  echo "Install Gmail dependencies in this environment: .venv/bin/pip install -r requirements-gmail.txt"
  exit 2
fi
exec .venv/bin/python -m mail_agent.web --db data/web-groq.sqlite3 "$@"
