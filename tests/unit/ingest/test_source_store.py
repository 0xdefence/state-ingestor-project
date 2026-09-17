"""Exact-byte source storage without interpreting or rereading input."""

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from services.application.ports import SourceStore
from services.infrastructure.source_store import FilesystemSourceStore


@pytest.mark.parametrize(
    "payload", [b"\xef\xbb\xbfA,\r\n\xf0\x9f\x8c\x9f\n", b"", b"\xff\x00 \r\n"]
)
def test_freeze_preserves_bytes_and_reuses_content(
    tmp_path: Path, payload: bytes
) -> None:
    store: SourceStore = FilesystemSourceStore(tmp_path / "sources")
    first = store.freeze(BytesIO(payload))
    second = store.freeze(BytesIO(payload))

    with store.open(first.locator) as frozen:
        assert frozen.read() == payload
    assert first.sha256 == hashlib.sha256(payload).hexdigest()
    assert first.byte_size == second.byte_size == len(payload)
    assert first.sha256 == second.sha256
    assert first.locator == second.locator == f"{first.sha256[:2]}/{first.sha256}"
    assert (first.reused, second.reused) == (False, True)
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [
        tmp_path / "sources" / first.locator
    ]


class ShortReadStream(BytesIO):
    def read(self, size: int | None = -1, /) -> bytes:
        assert size is not None and size > 0, "Source reads must be bounded"
        return super().read(min(size, 7))

    def seek(self, offset: int, whence: int = 0, /) -> int:
        raise AssertionError("The source must not be reread")


def test_freeze_streams_short_reads_once(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 100
    source = ShortReadStream(payload)
    store = FilesystemSourceStore(tmp_path)

    result = store.freeze(source)

    assert not source.closed
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.byte_size == len(payload)
    with store.open(result.locator) as frozen:
        assert frozen.read() == payload


def test_reuse_never_replaces_existing_object(tmp_path: Path) -> None:
    payload = b"existing immutable source\n"
    store = FilesystemSourceStore(tmp_path)
    first = store.freeze(BytesIO(payload))
    object_path = tmp_path / first.locator
    original_inode = object_path.stat().st_ino

    second = store.freeze(BytesIO(payload))

    assert second.reused
    assert object_path.stat().st_ino == original_inode
    assert object_path.read_bytes() == payload
