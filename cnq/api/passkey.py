"""Passkeys (WebAuthn Level 2) for the owner's closed panel: verify a new passkey and a passkey sign-in on the server.

Why not the py_webauthn package: the server's venv (/opt/cnq/venv) has only what deploy/api/install_api.sh put there (PyJWT,
cryptography, edge-tts) and the auto-deploy (the engine's git pull) never runs pip. This module needs nothing new: it uses
`cryptography` (already there) for the signatures and a small strict CBOR reader for the authenticator's data. It is checked
against py_webauthn's own published test vectors from real browsers (tests/test_passkey.py) and, end to end, against Chromium's
WebAuthn implementation with a virtual authenticator (tests/test_vip_auth_js.py).

Scope (what the panel asks the browser for, so what is accepted):
  • attestation "none": the authenticator's attestation statement is NOT checked — we do not restrict which device makes the
    passkey; the owner's own phone or Mac is trusted because the passkey can only be ADDED inside a fresh owner session
    (cnq/api/owner_auth.py). The format is recorded, nothing else.
  • user verification required (the UV flag = Face ID / Touch ID / device PIN actually ran), user presence required;
  • ES256 (-7, P-256), RS256 (-257, RSA ≥ 2048 bits) and EdDSA (-8, Ed25519) public keys;
  • the origin must be one of the allowed origins, the RP ID hash must match, crossOrigin must not be true (no iframes);
  • a sign counter that goes backwards (a cloned authenticator) is refused; 0 → 0 (Apple, Google passkeys) is allowed.
Biometrics never reach us: the device checks the face or finger itself and only signs the server's challenge.
Stdlib + cryptography."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import struct

ES256, RS256, EDDSA = -7, -257, -8
ALGS = (ES256, RS256, EDDSA)
MAX_CRED_ID = 1023
FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT, FLAG_ED = 0x01, 0x04, 0x08, 0x10, 0x40, 0x80


class PasskeyError(ValueError):
    """A response that must be refused. The message is a short English reason for logs/tests, never shown raw to the owner."""


# ---------------------------------------------------------------------------------------------------------------- encoding
def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(s) -> bytes:
    if not isinstance(s, str) or len(s) > 200_000:
        raise PasskeyError("not base64url")
    s = s.strip()
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in s):
        raise PasskeyError("not base64url")
    try:
        return base64.urlsafe_b64decode(s.rstrip("=") + "=" * (-len(s.rstrip("=")) % 4))
    except (binascii.Error, ValueError) as e:
        raise PasskeyError("not base64url") from e


# ---------------------------------------------------------------------------------------------------------------- CBOR
class _Cbor:
    """A strict reader of the CBOR subset WebAuthn uses (CTAP2 canonical form: definite lengths only)."""

    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.p = data, pos

    def _take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise PasskeyError("cbor: truncated")
        out = self.d[self.p:self.p + n]
        self.p += n
        return out

    def _arg(self, info: int) -> int:
        if info < 24:
            return info
        if info == 24:
            return self._take(1)[0]
        if info == 25:
            return struct.unpack(">H", self._take(2))[0]
        if info == 26:
            return struct.unpack(">I", self._take(4))[0]
        if info == 27:
            return struct.unpack(">Q", self._take(8))[0]
        raise PasskeyError("cbor: indefinite or reserved length")

    def item(self, depth: int = 0):
        if depth > 16:
            raise PasskeyError("cbor: too deep")
        ib = self._take(1)[0]
        major, info = ib >> 5, ib & 0x1F
        if major == 7:
            if info == 20:
                return False
            if info == 21:
                return True
            if info in (22, 23):
                return None
            if info == 25:
                h = struct.unpack(">H", self._take(2))[0]
                return struct.unpack(">e", struct.pack(">H", h))[0]
            if info == 26:
                return struct.unpack(">f", self._take(4))[0]
            if info == 27:
                return struct.unpack(">d", self._take(8))[0]
            raise PasskeyError("cbor: unsupported simple value")
        n = self._arg(info)
        if major == 0:
            return n
        if major == 1:
            return -1 - n
        if major == 2:
            return self._take(n)
        if major == 3:
            try:
                return self._take(n).decode("utf-8")
            except UnicodeDecodeError as e:
                raise PasskeyError("cbor: bad text") from e
        if major == 4:
            if n > 1024:
                raise PasskeyError("cbor: array too long")
            return [self.item(depth + 1) for _ in range(n)]
        if major == 5:
            if n > 256:
                raise PasskeyError("cbor: map too long")
            out = {}
            for _ in range(n):
                k = self.item(depth + 1)
                if not isinstance(k, (int, str)) or isinstance(k, bool):
                    raise PasskeyError("cbor: bad map key")
                if k in out:
                    raise PasskeyError("cbor: duplicate map key")
                out[k] = self.item(depth + 1)
            return out
        if major == 6:                              # a tag: its value is what matters here
            return self.item(depth + 1)
        raise PasskeyError("cbor: bad major type")


def cbor_decode(data: bytes, pos: int = 0) -> tuple[object, int]:
    """→ (value, the offset just after it)"""
    r = _Cbor(data, pos)
    return r.item(), r.p


def cbor_loads(data: bytes):
    v, end = cbor_decode(data)
    if end != len(data):
        raise PasskeyError("cbor: trailing bytes")
    return v


# ---------------------------------------------------------------------------------------------------------------- COSE keys
def cose_alg(cose: bytes) -> int:
    key = cbor_loads(cose)
    if not isinstance(key, dict) or key.get(3) not in ALGS:
        raise PasskeyError("unsupported key algorithm")
    return int(key[3])


def cose_public_key(cose: bytes):
    """COSE_Key bytes → a `cryptography` public key (the point / modulus is validated by cryptography)."""
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    key = cbor_loads(cose)
    if not isinstance(key, dict):
        raise PasskeyError("bad COSE key")
    kty, alg = key.get(1), key.get(3)
    try:
        if kty == 2 and alg == ES256:
            x, y = key.get(-2), key.get(-3)
            if key.get(-1) != 1 or not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
                raise PasskeyError("bad EC2 key")
            return ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
        if kty == 3 and alg == RS256:
            n, e = key.get(-1), key.get(-2)
            if not isinstance(n, bytes) or not isinstance(e, bytes) or len(n) * 8 < 2048 or len(n) > 1024 or len(e) > 8:
                raise PasskeyError("bad RSA key")
            return rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
        if kty == 1 and alg == EDDSA:
            x = key.get(-2)
            if key.get(-1) != 6 or not isinstance(x, bytes) or len(x) != 32:
                raise PasskeyError("bad OKP key")
            return ed25519.Ed25519PublicKey.from_public_bytes(x)
    except PasskeyError:
        raise
    except Exception as e:  # noqa: BLE001 — a point off the curve, a bad modulus …
        raise PasskeyError("invalid public key") from e
    raise PasskeyError("unsupported key type")


def verify_signature(cose: bytes, signature: bytes, data: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
    pub = cose_public_key(cose)
    try:
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))          # WebAuthn ES256 signatures are DER (ASN.1)
        elif isinstance(pub, rsa.RSAPublicKey):
            pub.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(pub, ed25519.Ed25519PublicKey):
            pub.verify(signature, data)
        else:
            raise PasskeyError("unsupported key")
    except InvalidSignature as e:
        raise PasskeyError("signature does not verify") from e
    except ValueError as e:
        raise PasskeyError("malformed signature") from e


# ---------------------------------------------------------------------------------------------------------------- parts
def parse_client_data(raw: bytes, typ: str, challenge: bytes, origins) -> dict:
    try:
        c = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PasskeyError("clientDataJSON is not JSON") from e
    if not isinstance(c, dict):
        raise PasskeyError("clientDataJSON is not an object")
    if c.get("type") != typ:
        raise PasskeyError("wrong ceremony type")
    try:
        got = unb64u(c.get("challenge"))
    except PasskeyError as e:
        raise PasskeyError("bad challenge") from e
    if not hmac.compare_digest(got, challenge):
        raise PasskeyError("challenge mismatch")
    if c.get("origin") not in set(origins):
        raise PasskeyError("origin not allowed")
    if c.get("crossOrigin") is True or c.get("topOrigin"):
        raise PasskeyError("cross-origin ceremony")
    return c


def parse_auth_data(ad: bytes, rp_id: str, require_uv: bool, attested: bool) -> dict:
    if len(ad) < 37:
        raise PasskeyError("authenticator data too short")
    if not hmac.compare_digest(ad[:32], hashlib.sha256(rp_id.encode()).digest()):
        raise PasskeyError("rp id mismatch")
    flags, count = ad[32], struct.unpack(">I", ad[33:37])[0]
    if not flags & FLAG_UP:
        raise PasskeyError("user not present")
    if require_uv and not flags & FLAG_UV:
        raise PasskeyError("user not verified")
    if flags & FLAG_BS and not flags & FLAG_BE:
        raise PasskeyError("backup state without backup eligibility")
    out = {"flags": flags, "sign_count": count, "uv": bool(flags & FLAG_UV), "backup_eligible": bool(flags & FLAG_BE),
           "backed_up": bool(flags & FLAG_BS)}
    pos = 37
    if attested:
        if not flags & FLAG_AT:
            raise PasskeyError("no attested credential data")
        if len(ad) < pos + 18:
            raise PasskeyError("attested credential data truncated")
        aaguid = ad[pos:pos + 16]
        n = struct.unpack(">H", ad[pos + 16:pos + 18])[0]
        pos += 18
        if n == 0 or n > MAX_CRED_ID or len(ad) < pos + n:
            raise PasskeyError("bad credential id length")
        cred_id = ad[pos:pos + n]
        pos += n
        _key, end = cbor_decode(ad, pos)
        cose = ad[pos:end]
        pos = end
        out.update(aaguid=aaguid, credential_id=cred_id, public_key=cose, alg=cose_alg(cose))
    elif flags & FLAG_AT:
        raise PasskeyError("unexpected attested credential data")
    if flags & FLAG_ED:
        _ext, pos = cbor_decode(ad, pos)
    if pos != len(ad):
        raise PasskeyError("trailing bytes in authenticator data")
    return out


def _cred(credential) -> dict:
    if isinstance(credential, (str, bytes)):
        try:
            credential = json.loads(credential)
        except ValueError as e:
            raise PasskeyError("credential is not JSON") from e
    if not isinstance(credential, dict) or credential.get("type") != "public-key" or not isinstance(credential.get("response"), dict):
        raise PasskeyError("not a public-key credential")
    return credential


def credential_id(credential) -> bytes:
    c = _cred(credential)
    raw = unb64u(c.get("rawId") or c.get("id") or "")
    if c.get("id") and unb64u(c["id"]) != raw:
        raise PasskeyError("id and rawId differ")
    if not raw or len(raw) > MAX_CRED_ID:
        raise PasskeyError("bad credential id")
    return raw


# ---------------------------------------------------------------------------------------------------------------- ceremonies
def verify_registration(credential, challenge: bytes, origins, rp_id: str, require_uv: bool = True, algs=ALGS) -> dict:
    """A new passkey (navigator.credentials.create) → {credential_id, public_key (COSE bytes), alg, sign_count, uv, backed_up,
    backup_eligible, aaguid (hex), fmt, transports}. Raises PasskeyError."""
    c = _cred(credential)
    r = c["response"]
    raw_id = credential_id(c)
    cdj = unb64u(r.get("clientDataJSON") or "")
    parse_client_data(cdj, "webauthn.create", challenge, origins)
    att = cbor_loads(unb64u(r.get("attestationObject") or ""))
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes) or not isinstance(att.get("fmt"), str):
        raise PasskeyError("bad attestation object")
    ad = parse_auth_data(att["authData"], rp_id, require_uv, attested=True)
    if ad["credential_id"] != raw_id:
        raise PasskeyError("credential id mismatch")
    if ad["alg"] not in algs:
        raise PasskeyError("algorithm not requested")
    cose_public_key(ad["public_key"])            # the key itself must be usable
    tr = r.get("transports") if isinstance(r.get("transports"), list) else c.get("transports") if isinstance(c.get("transports"), list) else []
    return {"credential_id": raw_id, "public_key": ad["public_key"], "alg": ad["alg"], "sign_count": ad["sign_count"],
            "uv": ad["uv"], "backed_up": ad["backed_up"], "backup_eligible": ad["backup_eligible"],
            "aaguid": ad["aaguid"].hex(), "fmt": att["fmt"][:32],
            "transports": [str(t)[:16] for t in tr if isinstance(t, str)][:8]}


def verify_authentication(credential, challenge: bytes, origins, rp_id: str, public_key: bytes, sign_count: int,
                          require_uv: bool = True, user_handle: bytes | None = None) -> dict:
    """A passkey sign-in (navigator.credentials.get) for a credential whose stored public key and counter are given →
    {credential_id, new_sign_count, uv, backed_up, user_handle}. Raises PasskeyError."""
    c = _cred(credential)
    r = c["response"]
    raw_id = credential_id(c)
    cdj = unb64u(r.get("clientDataJSON") or "")
    parse_client_data(cdj, "webauthn.get", challenge, origins)
    ad_raw = unb64u(r.get("authenticatorData") or "")
    ad = parse_auth_data(ad_raw, rp_id, require_uv, attested=False)
    uh = unb64u(r["userHandle"]) if r.get("userHandle") else None
    if uh is not None and user_handle is not None and not hmac.compare_digest(uh, user_handle):
        raise PasskeyError("user handle mismatch")
    verify_signature(public_key, unb64u(r.get("signature") or ""), ad_raw + hashlib.sha256(cdj).digest())
    new = ad["sign_count"]
    if (new or sign_count) and new <= sign_count:
        raise PasskeyError("sign counter did not increase (possible cloned authenticator)")
    return {"credential_id": raw_id, "new_sign_count": new, "uv": ad["uv"], "backed_up": ad["backed_up"], "user_handle": uh}
