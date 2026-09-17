"""Real filesystem publication, interruption, and concurrent reuse."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest

from services.application.ports import FrozenSource
from services.infrastructure.source_store import FilesystemSourceStore


class InterruptedStream(BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        if self.tell():
            raise OSError("source interrupted")
        return super().read(4 if size is None or size < 0 else min(size, 4))


def test_interrupted_freeze_removes_temporary_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    store = FilesystemSourceStore(tmp_path)

    with pytest.raises(OSError, match="source interrupted"):
        store.freeze(InterruptedStream(b"partial bytes never published"))

    assert list(tmp_path.rglob("*")) == []


class ConcurrentStream(BytesIO):
    def __init__(self, payload: bytes, barrier: Barrier) -> None:
        super().__init__(payload)
        self.barrier = barrier

    def read(self, size: int | None = -1, /) -> bytes:
        chunk = super().read(size)
        if not chunk:
            self.barrier.wait(timeout=10)
        return chunk


def test_concurrent_freezes_publish_one_complete_object(tmp_path: Path) -> None:
    payload = b"\xef\xbb\xbfA,\r\n\xf0\x9f\x8c\x9f\n" * 100_000
    barrier = Barrier(2)

    def freeze() -> FrozenSource:
        store = FilesystemSourceStore(tmp_path)
        with ConcurrentStream(payload, barrier) as source:
            result = store.freeze(source)
        with store.open(result.locator) as frozen:
            assert frozen.read() == payload
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(freeze) for _ in range(2)]
        first, second = [future.result(timeout=15) for future in futures]

    assert first.sha256 == second.sha256 == hashlib.sha256(payload).hexdigest()
    assert first.byte_size == second.byte_size == len(payload)
    assert first.locator == second.locator
    assert sorted([first.reused, second.reused]) == [False, True]
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [
        tmp_path / first.locator
    ]
