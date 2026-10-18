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


def test_access_codes_add_list_remove_with_fake_github():
    import base64, json
    from cnq.execution import access_codes as A
    store = {"doc": None, "sha": None}

    def http(method, url, hdr, data):
        if method == "GET":
            if store["doc"] is None:
                raise RuntimeError("404")
            return 200, {"sha": store["sha"], "content": base64.b64encode(json.dumps(store["doc"]).encode()).decode()}
        body = json.loads(data)
        store["doc"] = json.loads(base64.b64decode(body["content"]).decode()); store["sha"] = "s" + str(len(store["doc"]["codes"]))
        return 201, {}
    lines = []
    assert A.main(["add", "Роман"], env={"CNQ_GH_TOKEN": "t"}, http=http, out=lines.append) == 0
    code = lines[-1].split(":")[1].split()[0]
    assert code.startswith("NOVA-") and store["doc"]["codes"][0]["h"] == A.code_hash(code.lower()) and code not in json.dumps(store["doc"])
    assert A.main(["remove", "роман"], env={"CNQ_GH_TOKEN": "t"}, http=http, out=lines.append) == 0 and store["doc"]["codes"] == []
    assert A.main(["remove", "нет такого"], env={"CNQ_GH_TOKEN": "t"}, http=http, out=lines.append) == 1
