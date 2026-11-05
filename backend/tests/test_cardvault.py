import os

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.core.cardvault import (
    CardContext,
    CardOpener,
    CardSealer,
    VaultError,
    kid_of,
    mask,
    rewrap,
)

CTX = CardContext("t1", "p1", "c1")
K1, K2 = X25519PrivateKey.generate(), X25519PrivateKey.generate()
FP = os.urandom(32)


def sealer(active="k1"):
    return CardSealer({"k1": K1.public_key(), "k2": K2.public_key()}, active, FP)


def opener():
    return CardOpener({"k1": K1, "k2": K2})


def test_roundtrip_and_no_plaintext():
    blob = sealer().seal("ABCD-1234-EFGH", CTX)
    assert b"ABCD" not in blob
    assert opener().open(blob, CTX) == "ABCD-1234-EFGH"


def test_sealer_cannot_open():
    # The public tier holds only CardSealer, which has no decrypt capability.
    assert not hasattr(sealer(), "open")


def test_nonces_and_ephemeral_keys_are_random():
    s = sealer()
    assert s.seal("same", CTX) != s.seal("same", CTX)


@pytest.mark.parametrize(
    "other",
    [CardContext("t2", "p1", "c1"), CardContext("t1", "p2", "c1"), CardContext("t1", "p1", "c2")],
)
def test_row_swap_rejected(other):
    blob = sealer().seal("secret", CTX)
    with pytest.raises(VaultError):
        opener().open(blob, other)


@pytest.mark.parametrize("index", [-1, 3, 40, 80])
def test_tamper_rejected(index):
    blob = bytearray(sealer().seal("secret", CTX))
    blob[index] ^= 1
    with pytest.raises(VaultError):
        opener().open(bytes(blob), CTX)


def test_truncated_wrong_version_unknown_key_rejected():
    blob = sealer().seal("x", CTX)
    with pytest.raises(VaultError):
        opener().open(blob[:10], CTX)
    with pytest.raises(VaultError):
        opener().open(b"\x01" + blob[1:], CTX)
    with pytest.raises(VaultError):
        CardOpener({"zz": X25519PrivateKey.generate()}).open(blob, CTX)


def test_wrong_private_key_same_kid_rejected():
    blob = sealer().seal("x", CTX)
    with pytest.raises(VaultError):
        CardOpener({"k1": X25519PrivateKey.generate()}).open(blob, CTX)


def test_rotation():
    blob = sealer("k1").seal("secret", CTX)
    assert kid_of(blob) == "k1"
    blob2 = rewrap(blob, CTX, opener(), sealer("k2"))
    assert kid_of(blob2) == "k2"
    assert opener().open(blob2, CTX) == "secret"
    assert CardOpener({"k2": K2}).open(blob2, CTX) == "secret"


def test_fingerprint_is_tenant_scoped():
    s = sealer()
    assert s.fingerprint("t1", "X") == s.fingerprint("t1", " X ")
    assert s.fingerprint("t1", "X") != s.fingerprint("t2", "X")


def test_mask():
    assert mask("ABCD1234WXYZ") == "ABCD****WXYZ"
    assert mask("short") == "*****"
