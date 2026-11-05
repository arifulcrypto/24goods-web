from __future__ import annotations

import base64
import json
import logging
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.core.cardvault import CardOpener

log = logging.getLogger("app.delivery.keys")


class KeyFileError(RuntimeError):
    pass


def load_opener(path: str) -> CardOpener:
    if not path:
        raise KeyFileError("APP_VAULT_PRIVATE_KEYS_FILE is required")
    p = Path(path)
    try:
        mode = p.stat().st_mode
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise KeyFileError(f"cannot read private key file ({type(exc).__name__})") from None
    if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("private key file is group/world accessible; chmod 600 it")
    try:
        data = json.loads(raw)
        keys = {
            str(kid): X25519PrivateKey.from_private_bytes(base64.b64decode(b64, validate=True))
            for kid, b64 in data.items()
        }
    except (ValueError, AttributeError, TypeError):
        raise KeyFileError("private key file must be JSON {kid: base64 raw X25519 key}") from None
    finally:
        del raw
    return CardOpener(keys)
