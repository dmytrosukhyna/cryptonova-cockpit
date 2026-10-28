"""cnq/api/passkey.py — WebAuthn verification. The fixed vectors are real browser responses published with py_webauthn's own test
suite (github.com/duo-labs/py_webauthn, BSD-3-Clause, tests/test_verify_registration_response.py and
tests/test_verify_authentication_response.py); the rest are signed here with keys made by `cryptography`."""
import hashlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from cnq.api import passkey as pk

# ---- py_webauthn vectors (origin http://localhost:5000, rp id localhost) -------------------------------------------------
REG_NONE = {
    "id": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "rawId": "9y1xA8Tmg1FEmT-c7_fvWZ_uoTuoih3OvR45_oAK-cwHWhAbXrl2q62iLVTjiyEZ7O7n-CROOY494k7Q3xrs_w",
    "response": {
        "attestationObject": "o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRoRGF0YVjESZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2NFAAAAFwAAAAAAAAAAAAAAAAAAAAAAQPctcQPE5oNRRJk_nO_371mf7qE7qIodzr0eOf6ACvnMB1oQG165dqutoi1U44shGezu5_gkTjmOPeJO0N8a7P-lAQIDJiABIVggSFbUJF-42Ug3pdM8rDRFu_N5oiVEysPDB6n66r_7dZAiWCDUVnB39FlGypL-qAoIO9xWHtJygo2jfDmHl-_eKFRLDA",
        "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIiwiY2hhbGxlbmdlIjoiVHdON240V1R5R0tMYzRaWS1xR3NGcUtuSE00bmdscXN5VjBJQ0psTjJUTzlYaVJ5RnRya2FEd1V2c3FsLWdrTEpYUDZmbkYxTWxyWjUzTW00UjdDdnciLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9",
    },
    "type": "public-key", "clientExtensionResults": {}, "transports": ["nfc", "usb"],
}
REG_NONE_CHALLENGE = "TwN7n4WTyGKLc4ZY-qGsFqKnHM4nglqsyV0ICJlN2TO9XiRyFtrkaDwUvsql-gkLJXP6fnF1MlrZ53Mm4R7Cvw"
REG_NONE_KEY = "pQECAyYgASFYIEhW1CRfuNlIN6XTPKw0RbvzeaIlRMrDwwep-uq_-3WQIlgg1FZwd_RZRsqS_qgKCDvcVh7ScoKNo3w5h5fv3ihUSww"

AUTH_EC2 = {
    "id": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "rawId": "EDx9FfAbp4obx6oll2oC4-CZuDidRVV4gZhxC529ytlnqHyqCStDUwfNdm1SNHAe3X5KvueWQdAX3x9R1a2b9Q",
    "response": {
        "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MBAAAATg",
        "clientDataJSON": "eyJjaGFsbGVuZ2UiOiJ4aTMwR1BHQUZZUnhWRHBZMXNNMTBEYUx6VlFHNjZudi1fN1JVYXpIMHZJMll2RzhMWWdERW52TjVmWlpOVnV2RUR1TWk5dGUzVkxxYjQyTjBma0xHQSIsImNsaWVudEV4dGVuc2lvbnMiOnt9LCJoYXNoQWxnb3JpdGhtIjoiU0hBLTI1NiIsIm9yaWdpbiI6Imh0dHA6Ly9sb2NhbGhvc3Q6NTAwMCIsInR5cGUiOiJ3ZWJhdXRobi5nZXQifQ",
        "signature": "MEUCIGisVZOBapCWbnJJvjelIzwpixxIwkjCCb5aCHafQu68AiEA88v-2pJNNApPFwAKFiNuf82-2hBxYW5kGwVweeoxCwo",
    },
    "type": "public-key", "clientExtensionResults": {},
}
AUTH_EC2_CHALLENGE = "xi30GPGAFYRxVDpY1sM10DaLzVQG66nv-_7RUazH0vI2YvG8LYgDEnvN5fZZNVuvEDuMi9te3VLqb42N0fkLGA"
AUTH_EC2_KEY = "pQECAyYgASFYIIeDTe-gN8A-zQclHoRnGFWN8ehM1b7yAsa8I8KIvmplIlgg4nFGT5px8o6gpPZZhO01wdy9crDSA_Ngtkx0vGpvPHI"

AUTH_RSA = {
    "id": "ZoIKP1JQvKdrYj1bTUPJ2eTUsbLeFkv-X5xJQNr4k6s",
    "rawId": "ZoIKP1JQvKdrYj1bTUPJ2eTUsbLeFkv-X5xJQNr4k6s",
    "response": {
        "authenticatorData": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MFAAAAAQ",
        "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uZ2V0IiwiY2hhbGxlbmdlIjoiaVBtQWkxUHAxWEw2b0FncTNQV1p0WlBuWmExekZVRG9HYmFRMF9LdlZHMWxGMnMzUnRfM280dVN6Y2N5MHRtY1RJcFRUVDRCVTFULUk0bWFhdm5kalEiLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9",
        "signature": "iOHKX3erU5_OYP_r_9HLZ-CexCE4bQRrxM8WmuoKTDdhAnZSeTP0sjECjvjfeS8MJzN1ArmvV0H0C3yy_FdRFfcpUPZzdZ7bBcmPh1XPdxRwY747OrIzcTLTFQUPdn1U-izCZtP_78VGw9pCpdMsv4CUzZdJbEcRtQuRS03qUjqDaovoJhOqEBmxJn9Wu8tBi_Qx7A33RbYjlfyLm_EDqimzDZhyietyop6XUcpKarKqVH0M6mMrM5zTjp8xf3W7odFCadXEJg-ERZqFM0-9Uup6kJNLbr6C5J4NDYmSm3HCSA6lp2iEiMPKU8Ii7QZ61kybXLxsX4w4Dm3fOLjmDw",
        "userHandle": "T1RWa1l6VXdPRFV0WW1NNVlTMDBOVEkxTFRnd056Z3RabVZpWVdZNFpEVm1ZMk5p",
    },
    "type": "public-key", "clientExtensionResults": {},
}
AUTH_RSA_CHALLENGE = "iPmAi1Pp1XL6oAgq3PWZtZPnZa1zFUDoGbaQ0_KvVG1lF2s3Rt_3o4uSzccy0tmcTIpTTT4BU1T-I4maavndjQ"
AUTH_RSA_KEY = "pAEDAzkBACBZAQDfV20epzvQP-HtcdDpX-cGzdOxy73WQEvsU7Dnr9UWJophEfpngouvgnRLXaEUn_d8HGkp_HIx8rrpkx4BVs6X_B6ZjhLlezjIdJbLbVeb92BaEsmNn1HW2N9Xj2QM8cH-yx28_vCjf82ahQ9gyAr552Bn96G22n8jqFRQKdVpO-f-bvpvaP3IQ9F5LCX7CUaxptgbog1SFO6FI6ob5SlVVB00lVXsaYg8cIDZxCkkENkGiFPgwEaZ7995SCbiyCpUJbMqToLMgojPkAhWeyktu7TlK6UBWdJMHc3FPAIs0lH_2_2hKS-mGI1uZAFVAfW1X-mzKL0czUm2P1UlUox7IUMBAAE"

LOCAL = ["http://localhost:5000"]


def test_real_browser_registration_none_attestation():
    v = pk.verify_registration(REG_NONE, pk.unb64u(REG_NONE_CHALLENGE), LOCAL, "localhost")
    assert v["credential_id"] == pk.unb64u(REG_NONE["rawId"])
    assert v["public_key"] == pk.unb64u(REG_NONE_KEY)
    assert v["alg"] == pk.ES256 and v["sign_count"] == 23 and v["fmt"] == "none" and v["uv"] is True
    assert v["aaguid"] == "0" * 32 and v["backed_up"] is False and v["transports"] == ["nfc", "usb"]
    # the same response as a JSON string
    assert pk.verify_registration(json.dumps(REG_NONE), pk.unb64u(REG_NONE_CHALLENGE), LOCAL, "localhost")["sign_count"] == 23


@pytest.mark.parametrize("bad", ["challenge", "origin", "rp", "type", "id"])
def test_registration_refuses_wrong_context(bad):
    cred, ch, origins, rp = json.loads(json.dumps(REG_NONE)), pk.unb64u(REG_NONE_CHALLENGE), LOCAL, "localhost"
    if bad == "challenge":
        ch = b"x" * 32
    elif bad == "origin":
        origins = ["https://cryptonova.vip"]
    elif bad == "rp":
        rp = "cryptonova.vip"
    elif bad == "type":
        cred["type"] = "password"
    elif bad == "id":
        cred["rawId"] = cred["id"] = pk.b64u(b"other-id")
    with pytest.raises(pk.PasskeyError):
        pk.verify_registration(cred, ch, origins, rp)


def test_real_browser_assertion_ec2_and_rsa():
    # EC2 vector: user present but NOT verified → refused when UV is required (the panel always requires it)
    with pytest.raises(pk.PasskeyError, match="not verified"):
        pk.verify_authentication(AUTH_EC2, pk.unb64u(AUTH_EC2_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_EC2_KEY), 77)
    v = pk.verify_authentication(AUTH_EC2, pk.unb64u(AUTH_EC2_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_EC2_KEY), 77,
                                 require_uv=False)
    assert v["new_sign_count"] == 78 and v["uv"] is False
    # the counter must go up: stored 78 → the same response again is a replay / clone
    with pytest.raises(pk.PasskeyError, match="counter"):
        pk.verify_authentication(AUTH_EC2, pk.unb64u(AUTH_EC2_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_EC2_KEY), 78,
                                 require_uv=False)
    v = pk.verify_authentication(AUTH_RSA, pk.unb64u(AUTH_RSA_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_RSA_KEY), 0)
    assert v["new_sign_count"] == 1 and v["uv"] is True and v["user_handle"] == pk.unb64u(AUTH_RSA["response"]["userHandle"])
    # wrong key for the signature
    with pytest.raises(pk.PasskeyError, match="signature"):
        pk.verify_authentication(AUTH_RSA, pk.unb64u(AUTH_RSA_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_EC2_KEY), 0)
    # a different user handle than the one stored with the passkey
    with pytest.raises(pk.PasskeyError, match="user handle"):
        pk.verify_authentication(AUTH_RSA, pk.unb64u(AUTH_RSA_CHALLENGE), LOCAL, "localhost", pk.unb64u(AUTH_RSA_KEY), 0,
                                 user_handle=b"someone-else")


# ---- our own vectors: Ed25519 and ES256 made here, the exact bytes a browser sends -------------------------------------------
def _cbor(v) -> bytes:
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, fmt in ((24, ">B"), (25, ">H"), (26, ">I"), (27, ">Q")):
            if n < 1 << (8 * struct.calcsize(fmt)):
                return bytes([major << 5 | info]) + struct.pack(fmt, n)
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return head(3, len(b)) + b
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(_cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(_cbor(k) + _cbor(x) for k, x in v.items())
    raise TypeError(v)


def _make(key, alg, rp="cryptonova.vip", origin="https://cryptonova.vip", flags=0x45, count=0):
    if alg == pk.EDDSA:
        x = key.public_key().public_bytes_raw()
        cose = _cbor({1: 1, 3: -8, -1: 6, -2: x})
    else:
        n = key.public_key().public_numbers()
        cose = _cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
    cid = b"cred-" + bytes(range(11))
    ch = b"c" * 32
    rph = hashlib.sha256(rp.encode()).digest()
    ad = rph + bytes([flags]) + struct.pack(">I", count) + b"\x00" * 16 + struct.pack(">H", len(cid)) + cid + cose
    cdj = json.dumps({"type": "webauthn.create", "challenge": pk.b64u(ch), "origin": origin, "crossOrigin": False}).encode()
    reg = {"id": pk.b64u(cid), "rawId": pk.b64u(cid), "type": "public-key",
           "response": {"clientDataJSON": pk.b64u(cdj), "attestationObject": pk.b64u(_cbor({"fmt": "none", "attStmt": {}, "authData": ad}))}}
    return reg, ch, cose, cid, rph


def _assert(key, alg, cid, rph, count, flags=0x05, challenge=b"d" * 32, origin="https://cryptonova.vip", uh=b"owner-handle"):
    ad = rph + bytes([flags]) + struct.pack(">I", count)
    cdj = json.dumps({"type": "webauthn.get", "challenge": pk.b64u(challenge), "origin": origin}).encode()
    data = ad + hashlib.sha256(cdj).digest()
    sig = key.sign(data) if alg == pk.EDDSA else key.sign(data, ec.ECDSA(hashes.SHA256()))
    return {"id": pk.b64u(cid), "rawId": pk.b64u(cid), "type": "public-key",
            "response": {"clientDataJSON": pk.b64u(cdj), "authenticatorData": pk.b64u(ad), "signature": pk.b64u(sig),
                         "userHandle": pk.b64u(uh)}}


@pytest.mark.parametrize("alg", [pk.ES256, pk.EDDSA])
def test_own_vectors_register_then_sign_in(alg):
    key = ed25519.Ed25519PrivateKey.generate() if alg == pk.EDDSA else ec.generate_private_key(ec.SECP256R1())
    reg, ch, cose, cid, rph = _make(key, alg)
    v = pk.verify_registration(reg, ch, ["https://cryptonova.vip"], "cryptonova.vip")
    assert v["alg"] == alg and v["public_key"] == cose and v["credential_id"] == cid
    a = _assert(key, alg, cid, rph, 0)
    r = pk.verify_authentication(a, b"d" * 32, ["https://cryptonova.vip"], "cryptonova.vip", cose, 0, user_handle=b"owner-handle")
    assert r["new_sign_count"] == 0 and r["uv"]
    with pytest.raises(pk.PasskeyError, match="not verified"):        # Face ID did not run (UV flag off)
        pk.verify_authentication(_assert(key, alg, cid, rph, 0, flags=0x01), b"d" * 32, ["https://cryptonova.vip"], "cryptonova.vip", cose, 0)
    with pytest.raises(pk.PasskeyError, match="origin"):              # a phishing site's origin
        pk.verify_authentication(_assert(key, alg, cid, rph, 0, origin="https://cryptonova.vip.evil.io"), b"d" * 32,
                                 ["https://cryptonova.vip"], "cryptonova.vip", cose, 0)
    with pytest.raises(pk.PasskeyError, match="challenge"):
        pk.verify_authentication(a, b"e" * 32, ["https://cryptonova.vip"], "cryptonova.vip", cose, 0)


def test_registration_requires_uv_and_refuses_bad_auth_data():
    key = ec.generate_private_key(ec.SECP256R1())
    reg, ch, *_ = _make(key, pk.ES256, flags=0x41)                  # UP + AT, no UV
    with pytest.raises(pk.PasskeyError, match="not verified"):
        pk.verify_registration(reg, ch, ["https://cryptonova.vip"], "cryptonova.vip")
    reg, ch, *_ = _make(key, pk.ES256, flags=0x55)                  # BS without BE
    with pytest.raises(pk.PasskeyError, match="backup"):
        pk.verify_registration(reg, ch, ["https://cryptonova.vip"], "cryptonova.vip")
    reg, ch, *_ = _make(key, pk.ES256)
    obj = pk.cbor_loads(pk.unb64u(reg["response"]["attestationObject"]))
    obj["authData"] += b"\x00"                                       # trailing garbage after the key
    reg["response"]["attestationObject"] = pk.b64u(_cbor(obj))
    with pytest.raises(pk.PasskeyError, match="trailing"):
        pk.verify_registration(reg, ch, ["https://cryptonova.vip"], "cryptonova.vip")


def test_cbor_reader_is_strict():
    assert pk.cbor_loads(_cbor({1: [1, -2, b"x", "y", True]})) == {1: [1, -2, b"x", "y", True]}
    for bad in (b"\x9f\x01\xff",            # indefinite-length array
                b"\xa2\x01\x01\x01\x02",    # duplicate map key
                b"\x5a\xff\xff\xff\xff",    # byte string longer than the data
                b"\x01\x02"):               # trailing bytes
        with pytest.raises(pk.PasskeyError):
            pk.cbor_loads(bad)
    with pytest.raises(pk.PasskeyError):
        pk.unb64u("not base64!")
