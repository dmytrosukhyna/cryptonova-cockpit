"""The closed system's NOVA chat (web/app.html askLLM) — 21.10, owner: «AI-помощник не отвечает».
The browser call had no timeout, read every error body as JSON and asked gpt-oss for 600 tokens without reasoning effort
(the server's 20.10 incident: hidden thinking ate the budget → empty answer). These run the real function in node with a
fake fetch: abort after the timeout, one retry on 5xx, the provider's own error text, low effort for gpt-oss, empty answer."""
import json
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _block() -> str:
    s = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    i = s.index("const LLM_TIMEOUT_MS=")
    k = s.index("async function askLLM(", i)
    j = s.index("throw new Error(last||'нет ответа'); }", k) + len("throw new Error(last||'нет ответа'); }")
    return s[i:j]


HARNESS = r"""
const LLMP={groq:{url:'https://g/x',model:'openai/gpt-oss-120b',name:'Groq · бесплатно'},gemini:{url:'https://m/x',model:'gemini-x',name:'Google Gemini · бесплатно'},anthropic:{url:'https://a/x',model:'claude',name:'Anthropic · платно'}};
const SET={anth:'k',llm:'groq'}; const LS={}; let TOK=''; const sessionStorage={getItem:k=>k==='cnq_admin_token'?TOK:null}; const localStorage={getItem:()=>null}; const store=(k,v)=>{LS[k]=JSON.stringify(v)}; const load=(k,d)=>LS[k]?JSON.parse(LS[k]):d;
const console={warn(){} , log:globalThis.console.log};
let calls=[]; let script=[];
function resp(status,body,hdr){ return {ok:status>=200&&status<300,status,headers:{get:k=>(hdr||{})[k.toLowerCase()]||null},text:async()=>typeof body==='string'?body:JSON.stringify(body)}; }
globalThis.fetch=(url,o)=>{ calls.push(Object.assign(JSON.parse(o.body),{__url:url,__auth:(o.headers||{}).authorization||(o.headers||{})['x-api-key']})); const step=script.shift();
  if(step==='hang') return new Promise((res,rej)=>o.signal.addEventListener('abort',()=>{ const e=new Error('aborted'); e.name='AbortError'; rej(e); }));
  if(step==='neterr') return Promise.reject(new TypeError('Failed to fetch'));
  return Promise.resolve(resp(step[0],step[1],step[2])); };
__BLOCK__
const ok=t=>({choices:[{message:{content:t},finish_reason:'stop'}]});
(async()=>{ const R={};
  const run=async(name,steps,llm,opt)=>{ calls=[]; script=steps.slice(); SET.llm=llm||'groq'; const t0=Date.now();
    try{ R[name]={out:await askLLM('sys','q',600,opt),n:calls.length,body:calls[0],ms:Date.now()-t0}; }catch(e){ R[name]={err:e.message,n:calls.length,ms:Date.now()-t0}; } };
  await run('ok',[[200,ok('Привет')]]);
  await run('hang',['hang'],null,{timeout:300});
  await run('retry5xx',[[503,'<html>bad gateway</html>'],[200,ok('Ответ')]]);
  await run('gemini',[[400,[{error:{code:400,message:'API key not valid'}}]]],'gemini');
  await run('empty',[[200,{choices:[{message:{content:''},finish_reason:'length'}]}]]);
  await run('net',['neterr','neterr']);
  await run('anth',[[200,{content:[{type:'text',text:'Да'}],stop_reason:'end_turn'}]],'anthropic');
  // no browser key: the chat goes through the server with the «Управление» token; other callers still need a key
  SET.anth=''; TOK='tok123';
  await run('srv',[[200,{answer:'Через сервер',ms:900}]],'groq',{server:true});
  await run('srvRetry',[[200,{error:'rate_limit',ms:10}],[200,{answer:'Со второй',ms:900}]],'groq',{server:true});
  await run('srv403',[[403,{error:'admin_disabled'}]],'groq',{server:true});
  await run('srv413',[[413,{error:'too_large',limit_kb:30}]],'groq',{server:true});
  await run('noServerFlag',[[200,{answer:'x'}]],'groq',{});
  // 21.10 owner: an invalid browser key + a working «Управление» token → the chat retries through the server, then keeps using it
  SET.anth='bad'; await run('fallback',[[401,{error:{message:'Invalid API Key'}}],[200,{answer:'Через сервер',ms:700}]],'groq',{server:true});
  await run('afterBad',[[200,{answer:'Сразу сервер',ms:600}]],'groq',{server:true});
  await run('briefNoServer',[[401,{error:{message:'Invalid API Key'}}]],'groq',{});
  SET.anth='';
  TOK=''; await run('noRoute',[],'groq',{server:true});
  R.log=load('cnq_ai_log',[]).length;
  globalThis.console.log(JSON.stringify(R)); })();
"""


def test_panel_ai_call_states():
    node = shutil.which("node")
    if not node:
        return
    r = subprocess.run([node, "-e", HARNESS.replace("__BLOCK__", _block())], capture_output=True, text=True, timeout=60)
    R = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    assert R, r.stderr
    assert R["ok"]["out"] == "Привет" and R["ok"]["n"] == 1
    assert R["ok"]["body"]["reasoning_effort"] == "low" and R["ok"]["body"]["max_tokens"] >= 1500     # room for the answer
    assert R["hang"]["err"].startswith("нет ответа за") and R["hang"]["ms"] < 5000 and R["hang"]["n"] == 1   # never «…» forever
    assert R["retry5xx"]["out"] == "Ответ" and R["retry5xx"]["n"] == 2                                     # one retry on 5xx
    assert R["gemini"]["err"] == "API key not valid"       # the provider's own words
    assert "лимит длины" in R["empty"]["err"]
    assert R["net"]["err"] == "нет связи с Groq" and R["net"]["n"] == 2
    assert R["anth"]["out"] == "Да"
    assert R["fallback"]["out"] == "Через сервер" and R["fallback"]["n"] == 2          # key 401 → one retry through the server
    assert R["afterBad"]["out"] == "Сразу сервер" and R["afterBad"]["n"] == 1          # the session then goes straight to the server
    assert R["afterBad"]["body"]["__url"].endswith("/nova")
    assert R["briefNoServer"]["err"] == "Invalid API Key" and R["briefNoServer"]["n"] == 1   # a non-chat call never falls back to the server
    assert R["srv"]["out"] == "Через сервер" and R["srv"]["body"]["__url"].endswith("/v2/admin/nova")
    assert R["srv"]["body"]["__auth"] == "Bearer tok123" and R["srv"]["body"]["user"] == "q"
    assert R["srvRetry"]["out"] == "Со второй" and R["srvRetry"]["n"] == 2
    assert "токен «Управления»" in R["srv403"]["err"]
    assert "больше, чем принимает сервер" in R["srv413"]["err"] and R["srv413"]["n"] == 1     # 28.10: a 413 says so, no retry
    assert R["noServerFlag"]["err"] == "нет ключа" and R["noServerFlag"]["n"] == 0     # brief / translations never use the server key
    assert R["noRoute"]["err"] == "нет ключа"
    assert R["log"] >= 7


def test_panel_chat_has_states_and_retry():
    s = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    i = s.index("async function ask(q,bubble)")
    body = s[i:s.index("async function checkAI()", i)]
    assert "NOVA думает" in body and "Повторить" in body and "clearInterval(tick)" in body
    assert "a.textContent='…'" not in body                      # no bare ellipsis left waiting
    assert re.search(r'id="sAiBtn"', s) and "checkAI" in s       # Settings → «Проверить ИИ»
    assert "SET.anth" not in s[s.index("function aiLog"):s.index("function llmErrText")]   # the log never stores the key
    # 28.10 (build 46 §15): «Проверить ИИ» also lists this browser's last calls — status, time, error; never a key or a text
    a = s[s.index("function aiLogText"):s.index("async function checkAI0(")]
    assert "cnq_ai_log" in a and "SET.anth" not in a and "adminTok" not in a and "finally{ out.textContent+=aiLogText(6); }" in a


class _LLM:
    """a stand-in for cnq.agents.llm.LLM: available, counts calls, answers or raises"""
    model, provider = "openai/gpt-oss-120b", "groq"

    def __init__(self, answer="CRYPTO NOVA — это система.", exc=None):
        self.answer, self.exc, self.calls, self.last_finish, self.seen = answer, exc, 0, "stop", []

    @property
    def available(self):
        return True

    def complete(self, system, user, temperature=0.2):
        self.calls += 1
        self.seen.append(user)
        if self.exc:
            raise self.exc
        return self.answer


def test_agent_selftest_real_call_rate_limited(tmp_path):
    """GET /v2/agent/selftest (no login): the same model call as /v2/agent on a fixed question; one real call per 10 min."""
    import time as _t
    from cnq.api.auth import FirebaseVerifier
    from cnq.api.server import Api
    from cnq.api.store import Store
    web = tmp_path / "web"
    web.mkdir()
    (web / "app_home.json").write_text(json.dumps({"asof": int(_t.time() * 1000), "total": {"usd": 1}, "futures": {}, "spot": {}}))
    llm = _LLM()
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={}), str(web), llm)
    c, d = api.handle("GET", "/v2/agent/selftest", {}, b"")
    assert c == 200 and d["result"] == "ok" and d["cached"] is False and d["answer_head"].startswith("CRYPTO NOVA")
    assert d["model"] == "openai/gpt-oss-120b" and isinstance(d["ms"], int) and "Что такое CRYPTO NOVA" in llm.seen[0]
    c, d = api.handle("GET", "/v2/agent/selftest?lang=en", {}, b"")
    assert d["cached"] is True and llm.calls == 1                      # at most one real call per 10 minutes
    api.selftest["at"] -= 601
    llm.exc = RuntimeError("HTTP 401: invalid api key")
    c, d = api.handle("GET", "/v2/agent/selftest", {}, b"")
    assert c == 200 and d["result"] == "llm_error" and "401" in d["error"] and llm.calls == 2
    api.selftest["at"] -= 601
    llm.exc, llm.answer = None, "  "
    assert api.handle("GET", "/v2/agent/selftest", {}, b"")[1]["result"] == "empty"
    api.selftest, api.llm = None, None
    assert api.handle("GET", "/v2/agent/selftest", {}, b"")[1]["result"] == "not_configured"


def test_admin_nova_route_owner_only(tmp_path, monkeypatch=None):
    """POST /v2/admin/nova: the closed system's chat through the server's key — only with CNQ_ADMIN_TOKEN."""
    import time as _t
    from cnq.api.auth import FirebaseVerifier
    from cnq.api.server import Api
    from cnq.api.store import Store
    web = tmp_path / "web"
    web.mkdir()
    (web / "app_home.json").write_text(json.dumps({"asof": int(_t.time() * 1000)}))
    llm = _LLM(answer="BTC в бычьем режиме. Что делаем: ждать.")
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={}), str(web), llm)
    body = json.dumps({"system": "sys", "user": "Контекст… Вопрос: что по рынку?", "max_tokens": 600}).encode()
    keep = os.environ.pop("CNQ_ADMIN_TOKEN", None)
    try:
        assert api.handle("POST", "/v2/admin/nova", {}, body)[0] == 403                        # no token on the server
        os.environ["CNQ_ADMIN_TOKEN"] = "t" * 40
        assert api.handle("POST", "/v2/admin/nova", {"authorization": "Bearer wrong"}, body)[0] == 401
        c, d = api.handle("POST", "/v2/admin/nova", {"authorization": "Bearer " + "t" * 40}, body)
        assert c == 200 and d["answer"].startswith("BTC") and isinstance(d["ms"], int) and llm.seen[-1].endswith("что по рынку?")
        llm.exc = RuntimeError("HTTP 429: rate limit")
        c, d = api.handle("POST", "/v2/admin/nova", {"authorization": "Bearer " + "t" * 40}, body)
        assert c == 200 and d["error"] == "rate_limit" and api.panel_last["result"] == "rate_limit"
        assert api.handle("GET", "/v2/health", {}, b"")[1]["panel_last"]["result"] == "rate_limit"
    finally:
        os.environ.pop("CNQ_ADMIN_TOKEN", None)
        if keep is not None:
            os.environ["CNQ_ADMIN_TOKEN"] = keep


def test_panel_context_fits_the_server_cap():
    """28.10 (build 46): the chat's context stays within CTX_AI_MAX bytes (nova-panel-probe measured 12.7 KB against the server's
    old 16 KB cap); the coin on the chart and the portfolio stay, extra news / events / coins go first."""
    node = shutil.which("node")
    if not node:
        return
    s = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    i = s.index("const CTX_AI_MAX=")
    j = s.index("return C; }", i) + len("return C; }")
    js = (s[i:j] + r"""
const big=n=>'Ж'.repeat(n);
function ctxPack(){ return {now:'x',current:{sym:'SOL',price:1,levels:[1,2],advice:{head:'h',text:big(400),why:[big(300),big(300),big(300)]},
  events:Array.from({length:6},()=>({t:1,w:big(500),do:big(300)}))},coins:Array.from({length:20},(_,k)=>({sym:'C'+k,setup:{grade:big(200)}})),
  news:Array.from({length:6},()=>({title:big(600)})),portfolio:{positions:[{sym:'BTC'}],picks:{short:[big(800)],mid:[big(800)],long:[big(800)]}}}; }
const small=()=>({now:'x',current:{sym:'BTC'},coins:[{sym:'A'}],news:[{title:'n'}]});
const C=ctxForAI(); const S=(()=>{ const keep=ctxPack; ctxPack=small; const r=ctxForAI(); ctxPack=keep; return r; })();
console.log(JSON.stringify({bytes:ctxBytes(C),coins:C.coins.length,news:C.news.length,events:C.current.events.length,sym:C.current.sym,
  pos:C.portfolio.positions.length,picks:'picks' in C.portfolio,small:S.coins.length+S.news.length}));""")
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    R = json.loads(r.stdout.strip().splitlines()[-1])
    assert R["bytes"] <= 18000 and R["sym"] == "SOL" and R["pos"] == 1 and not R["picks"], R
    assert R["coins"] <= 15 and R["news"] <= 3 and R["events"] == 1, R
    assert R["small"] == 2                                                    # a small context is sent as it is
    # the server takes the panel's request up to 30 KB (nginx: 32 KB); the trimmed context (+ escaping + the system prompt) fits
    from cnq.api.server import PANEL_MAX_BODY
    assert PANEL_MAX_BODY == 30 * 1024 and 18000 * 1.25 + 4000 < PANEL_MAX_BODY
