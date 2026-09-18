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
admin = create_engine(url, isolation_level="AUTOCOMMIT")


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
        child = subprocess.Popen(["./scripts/run-local.sh"], env=env)
        try:
            raise SystemExit(child.wait())
        except KeyboardInterrupt:
            pass
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
finally:
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()
