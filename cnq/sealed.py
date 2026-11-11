"""Sealed owner files (11.11.2026, owner: the repo stays public — «всё, что можно — закрой»). Internal files the GitHub pipeline
makes for the owner are published only ENCRYPTED to the server's public key: web/sealed/<name>.json. The server (the API, the
engine) opens them with its private key and serves them to a signed-in owner (GET /v2/owner/sealed/<name>).

No secret has to be set by anyone: the server makes its own X25519 key pair on first use (<run>/data/owner_seal_key.pem, mode
600, never pushed) and shows only the public key (GET /v2/pubkey); the pipeline fetches it (scripts/ci_seal.py) and keeps a copy in
web/sealed/pub.json. Sealing: X25519 (ephemeral) → HKDF-SHA256 → AES-256-GCM. Needs the `cryptography` package on both sides;
without it nothing is sealed and the caller keeps its old behaviour."""
from __future__ import annotations

import base64
import json
import os
import time

INFO = b"cnq-sealed-v1"
KEY_FILE = "owner_seal_key.pem"
NAMES = ("owner_health", "gh_runs")          # sealed today; a name is added only when nothing in the pipeline reads its plaintext back


def available() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _key(data_dir: str):
    """the server's private key, made on first use"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    p = os.path.join(data_dir, KEY_FILE)
    try:
        with open(p, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    except OSError:
        pass
    k = X25519PrivateKey.generate()
    os.makedirs(data_dir, exist_ok=True)
    fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(p + ".tmp", 0o600)
    os.replace(p + ".tmp", p)
    return k


def public_key(data_dir: str) -> str:
    from cryptography.hazmat.primitives import serialization
    return _b64(_key(data_dir).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def _aes(shared: bytes, epk: bytes):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=epk, info=INFO).derive(shared))


def seal(pub_b64: str, data: bytes, now: int | None = None) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    eph = X25519PrivateKey.generate()
    epk = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    nonce = os.urandom(12)
    ct = _aes(eph.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))), epk).encrypt(nonce, data, INFO)
    return {"v": 1, "alg": "x25519-hkdf-sha256-aes256gcm", "asof": int(now or time.time() * 1000), "epk": _b64(epk), "nonce": _b64(nonce),
            "ct": _b64(ct)}


def open_(data_dir: str, blob: dict) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    epk = base64.b64decode(blob["epk"])
    return _aes(_key(data_dir).exchange(X25519PublicKey.from_public_bytes(epk)), epk).decrypt(base64.b64decode(blob["nonce"]),
                                                                                                base64.b64decode(blob["ct"]), INFO)


def load(web_dir: str, data_dir: str, name: str) -> dict | None:
    """web/sealed/<name>.json opened with the server's key; None when it is missing or cannot be opened"""
    try:
        with open(os.path.join(web_dir, "sealed", f"{name}.json"), encoding="utf-8") as f:
            blob = json.load(f)
        d = json.loads(open_(data_dir, blob).decode("utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None
