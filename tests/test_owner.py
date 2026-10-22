import json
from cnq.execution import ivan_watch, owner_setup


def test_owner_token_roundtrip_and_checks():
    blob = owner_setup.make_blob("  Dmytro ", "very-long-pass-123", now=1)
    assert set(blob) <= ivan_watch.BLOB_KEYS and "Dmytro" not in str(blob)
    d = ivan_watch.decrypt(blob, "very-long-pass-123")
    assert d["login"] == "dmytro" and d["role"] == "owner"
    try:
        ivan_watch.decrypt(blob, "wrong-pass-0000")
        assert False
    except ivan_watch.BadPassword:
        pass
    for bad in (("ab", "very-long-pass-123"), ("dmytro", "short")):
        try:
            owner_setup.make_blob(*bad)
            assert False
        except ValueError:
            pass


def test_owner_setup_cli_publishes_only_on_match():
    got = []
    answers = iter(["dmytro"])
    pws = iter(["correct horse battery", "correct horse battery"])
    rc = owner_setup.main(env={}, ask=lambda q: next(answers), ask_pw=lambda q: next(pws), put=lambda b: got.append(b) or True)
    assert rc == 0 and len(got) == 1 and ivan_watch.decrypt(got[0], "correct horse battery")["login"] == "dmytro"
    got.clear()
    pws = iter(["correct horse battery", "different one here"])
    rc = owner_setup.main(env={}, ask=lambda q: "dmytro", ask_pw=lambda q: next(pws), put=lambda b: got.append(b) or True)
    assert rc == 1 and not got


def test_access_codes_tool_talks_to_the_server_api():
    """22.10: no hash file in the public repo — the Mac tool calls the owner-only API, the server keeps salted PBKDF2 hashes."""
    import os
    from cnq.api import access as AC, console as C
    from cnq.api.store import Store
    from cnq.execution import access_codes as A
    st = Store(":memory:")
    con = C.Console(st, "web")
    con.access = AC.AccessCodes(st, legacy=[])
    tok = "t" * 40
    old = os.environ.get("CNQ_ADMIN_TOKEN")
    os.environ["CNQ_ADMIN_TOKEN"] = tok
    try:
        http = lambda method, url, hdr, data: con.handle(method, "/v2/admin/access", "", {k.lower(): v for k, v in hdr.items()}, data or b"")
        env = {"CNQ_ADMIN_TOKEN": tok}
        lines = []
        assert A.main(["add", "Роман"], env=env, http=http, out=lines.append) == 0
        code = lines[-1].split(":", 1)[1].split()[0]
        assert code.startswith("NOVA-") and len(code) == 19
        assert code not in json.dumps(st._q("SELECT * FROM access_codes"))                      # only a salted hash is kept
        assert con.access.verify(code)[1]["ok"] and con.access.verify(code.lower())[1]["ok"]
        assert A.main(["remove", "роман"], env=env, http=http, out=lines.append) == 0 and not con.access.verify(code)[1]["ok"]
        assert A.main(["remove", "нет такого"], env=env, http=http, out=lines.append) == 1
        assert A.main(["month", "Клиент"], env=env, http=http, out=lines.append) == 0 and "31 день" in lines[-1]
        c1 = lines[-1].split(":", 1)[1].split()[0]
        assert A.main(["month", "Клиент"], env=env, http=http, out=lines.append) == 0
        c2 = lines[-1].split(":", 1)[1].split()[0]
        assert not con.access.verify(c1)[1]["ok"] and con.access.verify(c2)[1]["ok"]          # a new month replaces the old code
        assert A.main(["list"], env=env, http=http, out=lines.append) == 0 and " · до " in lines[-1]
        assert A.main(["list"], env={"CNQ_ADMIN_TOKEN": "wrong" * 8}, http=http, out=lines.append) == 1
        assert A.main(["list"], env={}, http=http, out=lines.append) == 1 and "CNQ_ADMIN_TOKEN" in lines[-1]
    finally:
        if old is None:
            os.environ.pop("CNQ_ADMIN_TOKEN", None)
        else:
            os.environ["CNQ_ADMIN_TOKEN"] = old
