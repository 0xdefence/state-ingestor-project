#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://alexis:alexis@127.0.0.1:55432/alexis}"
export SOURCE_ROOT="${SOURCE_ROOT:-$PWD/var/sources}"
python="$PWD/.venv/bin/python"
[[ -x "$python" ]] || { echo 'Run uv sync --frozen first.' >&2; exit 1; }
command -v bun >/dev/null || { echo 'Install Bun first.' >&2; exit 1; }
[[ -d apps/web/node_modules ]] || { echo 'Run (cd apps/web && bun install --frozen-lockfile) first.' >&2; exit 1; }
"$python" - <<'PY'
import os
import socket
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
if url.host not in ('localhost', '127.0.0.1', '::1'):
    raise SystemExit('Local launch requires a loopback PostgreSQL host.')
for port in (8000, 5173):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
config = Config('alembic.ini')
config.set_main_option('sqlalchemy.url', os.environ['DATABASE_URL'].replace('%', '%%'))
command.upgrade(config, 'head')
PY
api_pid=''
web_pid=''
cleanup() {
  trap - EXIT INT TERM
  for pid in "$web_pid" "$api_pid"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "$web_pid" "$api_pid"; do
    if [[ -n "$pid" ]]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$python" -m uvicorn "${ALEXIS_API_APP:-services.api.app:create_app}" --factory --host 127.0.0.1 --port 8000 &
api_pid=$!
"$python" - <<'PY'
import time
from urllib.error import URLError
from urllib.request import urlopen
for _ in range(100):
    try:
        with urlopen('http://127.0.0.1:8000/api/health', timeout=1) as response:
            if response.status == 200:
                break
    except (OSError, URLError):
        time.sleep(0.1)
else:
    raise SystemExit('API did not become ready.')
PY
(cd apps/web && exec bun node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort) &
web_pid=$!
printf 'Local workspace: http://127.0.0.1:5173\n'
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do sleep 1; done
echo 'A local server exited unexpectedly.' >&2
exit 1
