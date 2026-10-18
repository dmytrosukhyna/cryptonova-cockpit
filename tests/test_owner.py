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
