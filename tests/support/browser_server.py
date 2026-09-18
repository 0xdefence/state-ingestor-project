"""Playwright-owned disposable database and local processes; never touches user data."""

import os
import signal
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
url = make_url(
    os.environ.get(
        "TEST_POSTGRES_URL",
        "postgresql+psycopg://alexis:alexis@127.0.0.1:55432/alexis",
    )
)
name = "browser_test_" + uuid4().hex
admin = create_engine(
    url,
    isolation_level="AUTOCOMMIT",
    connect_args={
        "connect_timeout": 2,
        "options": "-c statement_timeout=2000 -c lock_timeout=1000",
    },
)


def stop(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
with admin.connect() as connection:
    connection.execute(text(f'CREATE DATABASE "{name}"'))
try:
    with tempfile.TemporaryDirectory(prefix="alexis-browser-") as source_root:
        env = {
            **os.environ,
            "DATABASE_URL": url.set(database=name).render_as_string(
                hide_password=False
            ),
            "SOURCE_ROOT": source_root,
            "ALEXIS_API_APP": "tests.support.browser_api:create_app",
        }
        # Own a separate group so termination covers the shell, API and Vite,
        # without signalling this resource owner or Playwright itself.
        child = subprocess.Popen(
            ["./scripts/run-local.sh"], env=env, start_new_session=True
        )
        try:
            raise SystemExit(child.wait())
        except KeyboardInterrupt:
            pass
        finally:
            # Playwright's outer deadline is 10s. Spend at most 3s on the
            # process tree, reserving the remainder for source/database disposal.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            finally:
                # The shell can exit before a descendant, so stop the group
                # even when waiting for the direct child returned successfully.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=1)
finally:
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()
