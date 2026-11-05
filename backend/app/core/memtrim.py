"""Return freed heap pages to the OS (glibc). Keeps RSS low after large transient
allocations such as image decoding. No-op on other libcs."""

from __future__ import annotations

import ctypes
from functools import lru_cache


@lru_cache(maxsize=1)
def _libc() -> ctypes.CDLL | None:
    try:
        lib = ctypes.CDLL("libc.so.6")  # no find_library(): it shells out to ldconfig
    except OSError:
        return None
    return lib if hasattr(lib, "malloc_trim") else None


def trim() -> None:
    lib = _libc()
    if lib is not None:
        lib.malloc_trim(0)
