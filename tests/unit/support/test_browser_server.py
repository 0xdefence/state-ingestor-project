"""The acceptance owner must dispose resources before Playwright's hard deadline."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_stalled_server_tree_is_stopped_before_resource_cleanup_deadline(tmp_path):
    """A TERM-ignoring child AND grandchild cannot consume the outer 10s budget."""
    harness = tmp_path / "owner.py"
    harness.write_text(
        '''
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import sqlalchemy

root, output = map(Path, sys.argv[1:])
log = output / "lifecycle.txt"
def record(value):
    with log.open("a") as stream:
        stream.write(value + "\\n")
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, statement): record(str(statement))
class Engine:
    def connect(self): return Connection()
    def dispose(self): record("disposed")
sqlalchemy.create_engine = lambda *args, **kwargs: Engine()
original_popen = subprocess.Popen
stalled_tree = """
import os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    'print("ready", flush=True); time.sleep(60)'], stdout=subprocess.PIPE)
child.stdout.readline()
Path(sys.argv[1]).write_text(str(os.getpid()) + ',' + str(child.pid))
while True: time.sleep(1)
"""
def launch(command, **kwargs):
    (output / "source.txt").write_text(kwargs["env"]["SOURCE_ROOT"])
    return original_popen(
        [sys.executable, '-c', stalled_tree, str(output / "pids.txt")], **kwargs
    )
subprocess.Popen = launch
runpy.run_path(str(root / "tests/support/browser_server.py"), run_name="__main__")
'''
    )
    owner = subprocess.Popen(
        [sys.executable, str(harness), str(ROOT), str(tmp_path)],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    descendants = []
    try:
        ready_deadline = time.monotonic() + 5
        while not (tmp_path / "pids.txt").exists():
            if owner.poll() is not None:
                pytest.fail(f"Owner failed during startup: {owner.communicate()}")
            assert time.monotonic() < ready_deadline, "Stalled tree did not start"
            time.sleep(0.02)
        descendants = [
            int(pid) for pid in (tmp_path / "pids.txt").read_text().split(",")
        ]
        source = Path((tmp_path / "source.txt").read_text())
        assert source.is_dir()
        started = time.monotonic()
        # Playwright signals its entire owner group, then SIGKILLs at ten seconds.
        os.killpg(owner.pid, signal.SIGTERM)
        try:
            stdout, stderr = owner.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            pytest.fail("Owner failed to clean up before the outer shutdown deadline")
        assert owner.returncode == 0, (stdout, stderr)
        assert time.monotonic() - started < 8
        assert not source.exists()
        events = (tmp_path / "lifecycle.txt").read_text().splitlines()
        assert events[0].startswith('CREATE DATABASE "browser_test_')
        assert events[1].startswith('DROP DATABASE "browser_test_')
        assert events[2:] == ["disposed"]
        # Direct child is reaped by the owner; the OS reaps orphan descendants.
        gone_deadline = time.monotonic() + 1
        while True:
            alive = []
            for pid in descendants:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except ProcessLookupError:
                    pass
            if not alive:
                break
            assert time.monotonic() < gone_deadline, (
                f"Owned processes survived: {alive}"
            )
            time.sleep(0.02)
    finally:
        for pid in [*descendants, owner.pid]:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        owner.communicate()
        # Clean up the regression's own temp source if the pre-fix owner leaked it.
        if (tmp_path / "source.txt").exists():
            source = Path((tmp_path / "source.txt").read_text())
            if source.exists():
                source.rmdir()
