"""Local file store with random keys (user-supplied names never touch the disk)."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import anyio.to_thread

_KEY = re.compile(r"^[0-9a-f]{32}$")


class FileStore:
    def __init__(self, root: str):
        self.root = Path(root)

    def path_for(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("invalid storage key")
        return self.root / key[:2] / key

    def _write_sync(self, key: str, data: bytes) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

    def _delete_sync(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(self._delete_sync, key)

    async def write(self, data: bytes) -> str:
        key = secrets.token_hex(16)
        await anyio.to_thread.run_sync(self._write_sync, key, data)
        return key
