"""Build the seal-only vault for the API from PUBLIC key material.

Deliberately has no code path that reads private keys: the API process can add
stock but can never open it (docs/architecture.md §6, ADR-002).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

from app.core.cardvault import CardSealer
from app.core.config import Settings


class VaultConfigError(RuntimeError):
    pass


def _load_public_map(settings: Settings) -> dict[str, str]:
    if settings.vault_public_keys.strip():
        raw = settings.vault_public_keys
    elif settings.vault_public_keys_file:
        raw = Path(settings.vault_public_keys_file).read_text(encoding="utf-8")
    else:
        raise VaultConfigError("APP_VAULT_PUBLIC_KEYS or APP_VAULT_PUBLIC_KEYS_FILE is required")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VaultConfigError("vault public keys must be JSON {kid: base64}") from exc
    if not isinstance(data, dict) or not data:
        raise VaultConfigError("vault public keys must be a non-empty JSON object")
    return {str(k): str(v) for k, v in data.items()}


def build_sealer(settings: Settings) -> CardSealer:
    pubs: dict[str, X25519PublicKey] = {}
    for kid, b64 in _load_public_map(settings).items():
        try:
            raw = base64.b64decode(b64, validate=True)
            pubs[kid] = X25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise VaultConfigError(f"invalid public key for kid {kid!r}") from exc
    active = settings.vault_active_kid or (next(iter(pubs)) if len(pubs) == 1 else "")
    if not active:
        raise VaultConfigError("APP_VAULT_ACTIVE_KID is required when several keys are configured")
    try:
        fp_key = settings.fingerprint_key()
    except ValueError as exc:
        raise VaultConfigError("APP_VAULT_FINGERPRINT_KEY: " + str(exc)) from exc
    return CardSealer(pubs, active, fp_key)
