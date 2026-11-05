"""Card-key vault: asymmetric envelope encryption for activation codes.

Why asymmetric: the public-facing API / merchant-upload tier only holds the
*public* key, so it can SEAL new stock but can never OPEN it. Only the
isolated delivery worker holds private keys. A full compromise of the web
tier, a DB dump, a leaked backup or a SQL injection yields only ciphertext.

Scheme (ECIES-style, per record):
  * random 256-bit DEK; secret encrypted with AES-256-GCM(DEK)
  * DEK wrapped with AES-256-GCM under a key derived by
    HKDF-SHA256(X25519(ephemeral_priv, recipient_pub))
  * AEAD associated data binds the blob to (tenant, product, card_id), so a
    cheap card's ciphertext cannot be copied onto an expensive product row.

Blob layout (single BYTEA column):
  version(1) | kid_len(1) | kid | eph_pub(32) | wrap_nonce(12) | wrapped_dek(48)
  | data_nonce(12) | ciphertext+tag(16)

See docs/architecture.md §6 for key custody, rotation and delivery flow.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

VERSION = 2
_NONCE = 12
_PUB = 32
_WRAPPED_DEK = 32 + 16
_HKDF_INFO = b"24goods/cardvault/v2/wrap"


class VaultError(Exception):
    """Raised for any decryption / integrity failure. Never leaks details."""


@dataclass(frozen=True)
class CardContext:
    """Where a secret belongs. Becomes AEAD associated data."""

    tenant_id: str
    product_id: str
    card_id: str

    def aad(self) -> bytes:
        parts = (self.tenant_id, self.product_id, self.card_id)
        if any("\x1f" in p for p in parts):
            raise ValueError("context fields must not contain unit separator")
        return ("v2\x1f" + "\x1f".join(parts)).encode()


def _raw(pub: X25519PublicKey) -> bytes:
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _wrap_key(shared: bytes, eph_pub: bytes, rcpt_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=eph_pub + rcpt_pub, info=_HKDF_INFO
    ).derive(shared)


class CardSealer:
    """Encrypt-only. Safe to load in the public API / upload process."""

    def __init__(self, public_keys: dict[str, X25519PublicKey], active_kid: str, fp_key: bytes):
        if active_kid not in public_keys:
            raise ValueError("active key id not in keyring")
        if not active_kid or len(active_kid.encode()) > 255:
            raise ValueError("invalid key id")
        if len(fp_key) < 32:
            raise ValueError("fingerprint key must be >= 32 bytes")
        self._pubs = dict(public_keys)
        self._active = active_kid
        self._fp_key = fp_key

    @property
    def active_kid(self) -> str:
        return self._active

    def seal(self, secret: str, ctx: CardContext) -> bytes:
        aad = ctx.aad()
        rcpt_pub = _raw(self._pubs[self._active])
        eph = X25519PrivateKey.generate()
        eph_pub = _raw(eph.public_key())
        wrap_key = _wrap_key(eph.exchange(self._pubs[self._active]), eph_pub, rcpt_pub)

        dek = AESGCM.generate_key(bit_length=256)
        wrap_nonce, data_nonce = os.urandom(_NONCE), os.urandom(_NONCE)
        wrapped = AESGCM(wrap_key).encrypt(wrap_nonce, dek, aad)
        ciphertext = AESGCM(dek).encrypt(data_nonce, secret.encode(), aad)

        kid = self._active.encode()
        return b"".join(
            (bytes([VERSION, len(kid)]), kid, eph_pub, wrap_nonce, wrapped, data_nonce, ciphertext)
        )

    def fingerprint(self, tenant_id: str, secret: str) -> str:
        """Keyed, tenant-scoped fingerprint for UNIQUE(tenant_id, fingerprint).

        Rejects duplicate uploads without plaintext and without cross-tenant
        correlation. Note: holders of fp_key can confirm guesses, so
        low-entropy codes stay guessable to them — keep fp_key out of the DB.
        """
        msg = tenant_id.encode() + b"\x1f" + secret.strip().encode()
        return hmac.new(self._fp_key, msg, hashlib.sha256).hexdigest()

    def lookup_fingerprint(self, tenant_id: str, first_field: str) -> str:
        """Keyed fingerprint of a card's FIRST field (e.g. the account name), case-folded.

        Lets staff find a multi-field card by its account without any decryption. Same
        trust model as :meth:`fingerprint` (needs ``fp_key``; separate 0x1e domain).
        """
        msg = tenant_id.encode() + b"\x1e" + first_field.strip().casefold().encode()
        return hmac.new(self._fp_key, msg, hashlib.sha256).hexdigest()


def first_field(secret: str, separator: str) -> str:
    """First field of a card split by the product's delimiter (whole secret if absent)."""
    return secret.split(separator, 1)[0].strip() if separator else secret.strip()


def kid_of(blob: bytes) -> str:
    return blob[2 : 2 + blob[1]].decode()


class CardOpener:
    """Decrypt. Load ONLY in the isolated delivery worker."""

    def __init__(self, private_keys: dict[str, X25519PrivateKey]):
        if not private_keys:
            raise ValueError("empty keyring")
        self._privs = dict(private_keys)

    def open(self, blob: bytes, ctx: CardContext) -> str:
        try:
            if blob[0] != VERSION:
                raise VaultError("unsupported version")
            pos = 2 + blob[1]
            kid = blob[2:pos].decode()
            eph_pub = blob[pos : pos + _PUB]
            pos += _PUB
            wrap_nonce = blob[pos : pos + _NONCE]
            pos += _NONCE
            wrapped = blob[pos : pos + _WRAPPED_DEK]
            pos += _WRAPPED_DEK
            data_nonce = blob[pos : pos + _NONCE]
            ciphertext = blob[pos + _NONCE :]

            priv = self._privs[kid]
            shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
            wrap_key = _wrap_key(shared, eph_pub, _raw(priv.public_key()))
            aad = ctx.aad()
            dek = AESGCM(wrap_key).decrypt(wrap_nonce, wrapped, aad)
            return AESGCM(dek).decrypt(data_nonce, ciphertext, aad).decode()
        except VaultError:
            raise
        except Exception as exc:  # InvalidTag, KeyError, IndexError, ValueError, UnicodeError
            raise VaultError("card secret could not be opened") from exc


def rewrap(blob: bytes, ctx: CardContext, opener: CardOpener, sealer: CardSealer) -> bytes:
    """Key-rotation job: re-encrypt under the sealer's active key."""
    return sealer.seal(opener.open(blob, ctx), ctx)


def mask(secret: str, keep: int = 4) -> str:
    """Display-safe mask for admin lists and logs."""
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return secret[:keep] + "*" * (len(secret) - keep * 2) + secret[-keep:]
