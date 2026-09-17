"""Atomic, content-addressed storage of uninterpreted source bytes."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from services.application.ports import FrozenSource


class FilesystemSourceStore:
    """Store immutable objects under relative SHA-256 locators."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def freeze(self, content: BinaryIO) -> FrozenSource:
        digest = hashlib.sha256()
        byte_size = 0
        with NamedTemporaryFile(mode="wb", dir=self._root, prefix=".freeze-") as temp:
            while chunk := content.read(1024 * 1024):
                temp.write(chunk)
                digest.update(chunk)
                byte_size += len(chunk)
            temp.flush()
            os.fsync(temp.fileno())

            sha256 = digest.hexdigest()
            locator = f"{sha256[:2]}/{sha256}"
            target = self._root / locator
            target.parent.mkdir(exist_ok=True)
            try:
                # Same-filesystem linking publishes complete bytes without replacing
                # an existing object, even when multiple writers race to publish.
                os.link(temp.name, target)
            except FileExistsError:
                reused = True
            else:
                reused = False

        return FrozenSource(sha256, byte_size, locator, reused)

    def open(self, locator: str) -> BinaryIO:
        return (self._root / locator).open("rb")
