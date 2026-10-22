"""22.10 (audit 20.10): guest access codes are checked on the server — salted PBKDF2, throttled, no hash file in the public web/."""
import json
import os
import tempfile
from pathlib import Path

from cnq.api import access as AC
from cnq.api.auth import FirebaseVerifier
from cnq.api.server import Api
from cnq.api.store import Store


def test_new_codes_are_salted_pbkdf2_and_verify_case_insensitively():
    st = Store(":memory:")
    a = AC.AccessCodes(st, legacy=[])
    c1, c2 = a.add("Аня"), a.add("Борис", days=31)
    assert c1 != c2 and len(c1) == 19 and c1.count("-") == 3
    rows = st._q("SELECT id, salt, hash FROM access_codes")
    assert len({r[1] for r in rows}) == 2 and all(c1 not in json.dumps(r) and c2 not in json.dumps(r) for r in rows)
    code, out = a.verify(" " + c1.lower() + " ")
    assert code == 200 and out == {"ok": True, "level": "tests", "label": "Аня", "until": None}
    assert a.verify(c2)[1]["until"] and a.verify("NOVA-AAAA-BBBB-CCCC")[1] == {"ok": False, "error": "unknown_code"}


def test_expired_code_is_refused():
    a = AC.AccessCodes(Store(":memory:"), legacy=[])
    c = a.add("Месяц", days=31, now=1_000)
    assert a.verify(c, now=1_000 + 32 * 86_400_000)[1]["error"] == "expired"


def test_wrong_codes_are_throttled_per_address():
    a = AC.AccessCodes(Store(":memory:"), legacy=[])
    good = a.add("x")
    for _ in range(AC.FAIL_MAX):
        assert a.verify("NOVA-ZZZZ-ZZZZ-ZZZZ", ip="9.9.9.9")[0] == 200
    code, out = a.verify(good, ip="9.9.9.9")
    assert code == 429 and out["error"] == "too_many_attempts"
    assert a.verify(good, ip="8.8.8.8")[1]["ok"]                                   # another address is not affected


def test_legacy_codes_keep_working_until_removed_and_never_come_back():
    st = Store(":memory:")
    legacy = [(AC._legacy_hash("NOVA-AB12-CD34"), "old", "tests", 1)]
    a = AC.AccessCodes(st, legacy=legacy)
    assert a.verify("nova-ab12-cd34")[1]["ok"] and [c["legacy"] for c in a.list()] == [True]
    assert a.remove("legacy") == 1 and not a.verify("NOVA-AB12-CD34")[1]["ok"] and a.list() == []
    AC.AccessCodes(st, legacy=legacy)                                                # a restart does not import them again
    assert not AC.AccessCodes(st, legacy=legacy).verify("NOVA-AB12-CD34")[1]["ok"]


def test_the_api_route_and_no_public_hash_file():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "web").mkdir()
    st = Store(str(tmp / "api.sqlite"))
    api = Api(st, FirebaseVerifier("crypto-nova", keys={}), str(tmp / "web"))
    code = api.access.add("Тест")
    st_, out = api.handle("POST", "/v2/access/verify", {"x-real-ip": "1.1.1.1"}, json.dumps({"code": code}).encode())
    assert st_ == 200 and out["ok"] and out["label"] == "Тест"
    assert api.handle("POST", "/v2/access/verify", {}, b"{bad")[0] == 400
    assert api.handle("GET", "/v2/admin/access", {}, b"")[0] in (401, 403)       # the list needs the owner's token
    repo = Path(__file__).resolve().parents[1]
    assert not (repo / "web" / "access.json").exists()                             # 22.10: removed from the public site
    assert "TESTS_HASH" not in (repo / "web" / "app.html").read_text(encoding="utf-8")


def test_admin_console_issues_and_lists_codes_with_the_token():
    from cnq.api import console as C
    st = Store(":memory:")
    con = C.Console(st, "web")
    con.access = AC.AccessCodes(st, legacy=[])
    tok = "k" * 40
    old = os.environ.get("CNQ_ADMIN_TOKEN")
    os.environ["CNQ_ADMIN_TOKEN"] = tok
    try:
        h = {"authorization": "Bearer " + tok}
        assert con.handle("POST", "/v2/admin/access", "", h, json.dumps({"op": "add", "label": "Вика"}).encode())[1]["error"] == "confirm_required"
        code, out = con.handle("POST", "/v2/admin/access", "", h, json.dumps({"op": "add", "label": "Вика", "confirm": True}).encode())
        assert code == 200 and out["code"].startswith("NOVA-") and con.access.verify(out["code"])[1]["ok"]
        assert con.handle("GET", "/v2/admin/access", "", h, b"")[1]["codes"][0]["label"] == "Вика"
    finally:
        if old is None:
            os.environ.pop("CNQ_ADMIN_TOKEN", None)
        else:
            os.environ["CNQ_ADMIN_TOKEN"] = old
