//+------------------------------------------------------------------+
//| CryptoNovaBridge.mq5 — CRYPTO NOVA AI observation bridge          |
//| Sends the account's deals, SL/TP changes, a heartbeat with open    |
//| positions and the market context (M5/H1/D1 bars, the day's range, |
//| the MT5 economic calendar ±24 h) to the CRYPTO NOVA server.        |
//| READ-ONLY: this EA never opens, closes or modifies an order.       |
//| The login is sent only as a SHA-256 hash; no password is needed.  |
//+------------------------------------------------------------------+
#property copyright "CRYPTO NOVA AI"
#property version   "1.00"
#property strict

input string InpUrl          = "https://app.crypto-capital.app/v2/peter/ingest"; // Server URL (allow it in Tools → Options → Expert Advisors)
input string InpToken        = "";       // Personal token from CRYPTO NOVA (paste here)
input int    InpHeartbeatMin = 5;        // Heartbeat every N minutes
input int    InpM5Bars       = 24;       // Closed M5 bars per heartbeat (per symbol)
input int    InpCtxBarsHD    = 210;      // H1 / D1 bars in the context of each entry / exit
input int    InpBackfillDays = 365;      // History sent once on the first start (0 = none)
input string InpExtraSymbols = "XAUUSD"; // Symbols always watched (comma separated)
input string InpCtxSymbols   = "USDX,DXY,DX,US10Y,UST10Y,TNX"; // DXY / US10Y if your broker has them (missing ones are skipped)

#define QUEUE_FILE   "cnq_bridge_queue.txt"
#define STATE_FILE   "cnq_bridge_state.txt"
#define MAX_QUEUE    2000
#define BATCH        20

string   g_acct = "";
long     g_tzoff = 0;            // broker server time − UTC, seconds
datetime g_lastHb = 0;
datetime g_lastM5[];             // per watched symbol: the last M5 bar already sent
string   g_syms[];
int      g_failStreak = 0;
datetime g_nextTry = 0;

//--------------------------------------------------------------------- helpers
string Esc(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\n", " ");
   StringReplace(s, "\r", " ");
   return s;
  }

string D(double v, int digits = 5) { return DoubleToString(v, digits); }

long UtcMs(datetime brokerTime) { return ((long)brokerTime - g_tzoff) * 1000; }

string Sha256Hex(string text)
  {
   uchar src[], key[], res[];
   StringToCharArray(text, src, 0, StringLen(text), CP_UTF8);
   if(CryptEncode(CRYPT_HASH_SHA256, src, key, res) <= 0) return "";
   string h = "";
   for(int i = 0; i < ArraySize(res); i++) h += StringFormat("%02x", res[i]);
   return h;
  }

void UpdateTz()
  {
   long off = (long)TimeTradeServer() - (long)TimeGMT();
   g_tzoff = (long)MathRound(off / 1800.0) * 1800;   // brokers use whole / half hours
  }

bool HasSym(string s) { for(int i = 0; i < ArraySize(g_syms); i++) if(g_syms[i] == s) return true; return false; }

void AddSym(string s)
  {
   if(s == "" || HasSym(s) || !SymbolSelect(s, true)) return;
   int n = ArraySize(g_syms);
   ArrayResize(g_syms, n + 1); ArrayResize(g_lastM5, n + 1);
   g_syms[n] = s; g_lastM5[n] = 0;
  }

string BarsJson(string sym, ENUM_TIMEFRAMES tf, int count, datetime after = 0)
  {
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int got = CopyRates(sym, tf, 1, count, r);      // shift 1: closed bars only
   string out = "[";
   bool first = true;
   for(int i = 0; i < got; i++)
     {
      if(after > 0 && r[i].time <= after) continue;
      if(!first) out += ",";
      first = false;
      out += StringFormat("[%I64d,%s,%s,%s,%s]", UtcMs(r[i].time), D(r[i].open), D(r[i].high), D(r[i].low), D(r[i].close));
     }
   return out + "]";
  }

string CalendarJson(datetime around)
  {
   MqlCalendarValue v[];
   string out = "[";
   if(CalendarValueHistory(v, around - 86400, around + 86400) > 0)
     {
      int n = 0;
      for(int i = 0; i < ArraySize(v) && n < 40; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(v[i].event_id, ev)) continue;
         if(ev.importance < CALENDAR_IMPORTANCE_MODERATE) continue;
         MqlCalendarCountry c;
         string cur = CalendarCountryById(ev.country_id, c) ? c.currency : "";
         if(cur != "USD" && cur != "EUR" && cur != "CNY" && cur != "GBP" && cur != "JPY") continue;
         if(n > 0) out += ",";
         out += StringFormat("{\"t\":%I64d,\"cur\":\"%s\",\"name\":\"%s\",\"imp\":%d}", UtcMs(v[i].time), cur, Esc(ev.name), (int)ev.importance);
         n++;
        }
     }
   return out + "]";
  }

string ExtJson()
  {
   string parts[];
   int k = StringSplit(InpCtxSymbols, ',', parts);
   string out = "{";
   bool first = true;
   for(int i = 0; i < k; i++)
     {
      string s = parts[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(s == "" || !SymbolSelect(s, true)) continue;
      double last = SymbolInfoDouble(s, SYMBOL_BID);
      double prev = iClose(s, PERIOD_D1, 1);
      if(last <= 0) continue;
      if(!first) out += ",";
      first = false;
      out += StringFormat("\"%s\":{\"last\":%s,\"chg_d1_pct\":%s}", Esc(s), D(last), prev > 0 ? D((last / prev - 1) * 100, 3) : "null");
     }
   return out + "}";
  }

string CtxJson(string sym, datetime when)
  {
   return StringFormat("{\"M5\":%s,\"H1\":%s,\"D1\":%s,\"day\":{\"open\":%s,\"high\":%s,\"low\":%s},\"calendar\":%s,\"ext\":%s}",
                       BarsJson(sym, PERIOD_M5, 48), BarsJson(sym, PERIOD_H1, InpCtxBarsHD), BarsJson(sym, PERIOD_D1, InpCtxBarsHD),
                       D(iOpen(sym, PERIOD_D1, 0)), D(iHigh(sym, PERIOD_D1, 0)), D(iLow(sym, PERIOD_D1, 0)),
                       CalendarJson(when), ExtJson());
  }

string EntryName(long e)
  {
   if(e == DEAL_ENTRY_IN) return "in";
   if(e == DEAL_ENTRY_OUT) return "out";
   if(e == DEAL_ENTRY_INOUT) return "inout";
   if(e == DEAL_ENTRY_OUT_BY) return "out_by";
   return "other";
  }

string TypeName(long t)
  {
   if(t == DEAL_TYPE_BUY) return "buy";
   if(t == DEAL_TYPE_SELL) return "sell";
   if(t == DEAL_TYPE_BALANCE) return "balance";
   if(t == DEAL_TYPE_CREDIT) return "credit";
   return "other";
  }

double DealEffect(ulong d)
  {
   return HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
          HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
  }

string DealJson(ulong d, string kind, double balAfter, bool withCtx)
  {
   string sym = HistoryDealGetString(d, DEAL_SYMBOL);
   datetime t = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
   string j = StringFormat("{\"type\":\"%s\",\"deal\":%I64u,\"order\":%I64d,\"pos\":%I64d,\"symbol\":\"%s\",\"dtype\":\"%s\",\"entry\":\"%s\","
                           "\"volume\":%s,\"price\":%s,\"sl\":%s,\"tp\":%s,\"profit\":%s,\"swap\":%s,\"commission\":%s,\"fee\":%s,"
                           "\"t\":%I64d,\"bal_after\":%s,\"magic\":%I64d",
                           kind, d, HistoryDealGetInteger(d, DEAL_ORDER), HistoryDealGetInteger(d, DEAL_POSITION_ID), Esc(sym),
                           TypeName(HistoryDealGetInteger(d, DEAL_TYPE)), EntryName(HistoryDealGetInteger(d, DEAL_ENTRY)),
                           D(HistoryDealGetDouble(d, DEAL_VOLUME), 2), D(HistoryDealGetDouble(d, DEAL_PRICE)), D(HistoryDealGetDouble(d, DEAL_SL)),
                           D(HistoryDealGetDouble(d, DEAL_TP)), D(HistoryDealGetDouble(d, DEAL_PROFIT), 2), D(HistoryDealGetDouble(d, DEAL_SWAP), 2),
                           D(HistoryDealGetDouble(d, DEAL_COMMISSION), 2), D(HistoryDealGetDouble(d, DEAL_FEE), 2),
                           UtcMs(t), D(balAfter, 2), HistoryDealGetInteger(d, DEAL_MAGIC));
   if(kind == "deal")
      j += StringFormat(",\"balance\":%s,\"equity\":%s", D(AccountInfoDouble(ACCOUNT_BALANCE), 2), D(AccountInfoDouble(ACCOUNT_EQUITY), 2));
   if(withCtx && sym != "") j += ",\"ctx\":" + CtxJson(sym, t);
   return j + "}";
  }

//--------------------------------------------------------------------- queue (one JSON event per line, survives restarts)
int QueueCount()
  {
   int h = FileOpen(QUEUE_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE) return 0;
   int n = 0;
   while(!FileIsEnding(h)) { if(StringLen(FileReadString(h)) > 0) n++; }
   FileClose(h);
   return n;
  }

void Enqueue(string ev)
  {
   if(QueueCount() >= MAX_QUEUE) { Print("CryptoNovaBridge: queue full, the oldest events stay, this one is dropped"); return; }
   int h = FileOpen(QUEUE_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE) { Print("CryptoNovaBridge: cannot open the queue file, error ", GetLastError()); return; }
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, ev + "\n");
   FileClose(h);
  }

int Post(string body)
  {
   char data[], res[];
   string resHeaders;
   int n = StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0) ArrayResize(data, n - 1);              // drop the trailing \0
   string headers = "Content-Type: application/json\r\nX-CNQ-Token: " + InpToken + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", InpUrl, headers, 15000, data, res, resHeaders);
   if(code == -1)
     {
      int err = GetLastError();
      if(err == 4014) Print("CryptoNovaBridge: add ", InpUrl, " in Tools → Options → Expert Advisors → «Allow WebRequest for listed URL»");
      else Print("CryptoNovaBridge: no connection (", err, "), the events wait in the queue");
     }
   else if(code == 401) Print("CryptoNovaBridge: the token is not accepted — check InpToken");
   return code;
  }

void Flush()
  {
   if(TimeLocal() < g_nextTry) return;
   string lines[];
   int h = FileOpen(QUEUE_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   int n = 0;
   while(!FileIsEnding(h)) { string s = FileReadString(h); if(StringLen(s) > 0) { ArrayResize(lines, n + 1); lines[n++] = s; } }
   FileClose(h);
   if(n == 0) return;
   int sent = 0;
   while(sent < n)
     {
      int k = MathMin(BATCH, n - sent);
      string body = "{\"v\":1,\"acct\":\"" + g_acct + "\",\"events\":[";
      for(int i = 0; i < k; i++) body += (i ? "," : "") + lines[sent + i];
      body += "]}";
      int code = Post(body);
      if(code != 200 && !(code == 400 || code == 413))       // a broken event is dropped, anything else is retried later
        {
         g_failStreak++;
         int sh = g_failStreak > 6 ? 6 : g_failStreak;
         int wait = 30 * (1 << sh);
         g_nextTry = TimeLocal() + (wait > 1800 ? 1800 : wait);
         break;
        }
      g_failStreak = 0;
      sent += k;
     }
   // keep only what was not sent
   h = FileOpen(QUEUE_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   for(int i = sent; i < n; i++) FileWriteString(h, lines[i] + "\n");
   FileClose(h);
  }

//--------------------------------------------------------------------- events
void SendHeartbeat()
  {
   UpdateTz();
   string pos = "[";
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || !PositionSelectByTicket(tk)) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      AddSym(sym);
      if(StringLen(pos) > 1) pos += ",";
      pos += StringFormat("{\"pos\":%I64d,\"symbol\":\"%s\",\"side\":\"%s\",\"volume\":%s,\"open_price\":%s,\"price\":%s,\"sl\":%s,\"tp\":%s,"
                          "\"profit\":%s,\"swap\":%s,\"open_t\":%I64d}",
                          PositionGetInteger(POSITION_IDENTIFIER), Esc(sym), PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "buy" : "sell",
                          D(PositionGetDouble(POSITION_VOLUME), 2), D(PositionGetDouble(POSITION_PRICE_OPEN)), D(PositionGetDouble(POSITION_PRICE_CURRENT)),
                          D(PositionGetDouble(POSITION_SL)), D(PositionGetDouble(POSITION_TP)), D(PositionGetDouble(POSITION_PROFIT), 2),
                          D(PositionGetDouble(POSITION_SWAP), 2), UtcMs((datetime)PositionGetInteger(POSITION_TIME)));
     }
   pos += "]";
   string cs = "{";
   for(int i = 0; i < ArraySize(g_syms); i++)
     {
      if(i) cs += ",";
      cs += StringFormat("\"%s\":{\"M5\":%s}", Esc(g_syms[i]), BarsJson(g_syms[i], PERIOD_M5, InpM5Bars, g_lastM5[i]));
      g_lastM5[i] = iTime(g_syms[i], PERIOD_M5, 1);
     }
   cs += "}";
   Enqueue(StringFormat("{\"type\":\"heartbeat\",\"t\":%I64d,\"tz_off\":%I64d,\"balance\":%s,\"equity\":%s,\"currency\":\"%s\",\"leverage\":%I64d,"
                        "\"terminal\":\"MT5 build %d\",\"positions\":%s,\"candles\":%s}",
                        UtcMs(TimeTradeServer()), g_tzoff, D(AccountInfoDouble(ACCOUNT_BALANCE), 2), D(AccountInfoDouble(ACCOUNT_EQUITY), 2),
                        Esc(AccountInfoString(ACCOUNT_CURRENCY)), AccountInfoInteger(ACCOUNT_LEVERAGE), (int)TerminalInfoInteger(TERMINAL_BUILD), pos, cs));
   g_lastHb = TimeLocal();
   Flush();
  }

void Backfill()
  {
   if(InpBackfillDays <= 0) return;
   int h = FileOpen(STATE_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h != INVALID_HANDLE) { string s = FileReadString(h); FileClose(h); if(s == g_acct) return; }   // already sent for this account
   datetime to = TimeCurrent(), from = to - InpBackfillDays * 86400;
   if(!HistorySelect(from, to)) return;
   int n = HistoryDealsTotal();
   double running = AccountInfoDouble(ACCOUNT_BALANCE);    // walk back from today's balance: the balance right after each deal
   string evs[];
   ArrayResize(evs, n);
   int k = 0;
   for(int i = n - 1; i >= 0; i--)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0) continue;
      double eff = DealEffect(d);
      long typ = HistoryDealGetInteger(d, DEAL_TYPE);
      if(typ == DEAL_TYPE_BUY || typ == DEAL_TYPE_SELL) { evs[k++] = DealJson(d, "history", running, false); AddSym(HistoryDealGetString(d, DEAL_SYMBOL)); }
      running -= eff;
     }
   for(int i = k - 1; i >= 0; i--) Enqueue(evs[i]);       // oldest first
   h = FileOpen(STATE_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h != INVALID_HANDLE) { FileWriteString(h, g_acct); FileClose(h); }
   Print("CryptoNovaBridge: history queued, deals: ", k);
  }

int OnInit()
  {
   if(StringLen(InpToken) < 24) { Alert("CryptoNovaBridge: paste the token from CRYPTO NOVA into InpToken"); return INIT_PARAMETERS_INCORRECT; }
   UpdateTz();
   g_acct = Sha256Hex("cnq:" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ":" + AccountInfoString(ACCOUNT_SERVER));
   string parts[];
   int k = StringSplit(InpExtraSymbols, ',', parts);
   for(int i = 0; i < k; i++) { string s = parts[i]; StringTrimLeft(s); StringTrimRight(s); AddSym(s); }
   AddSym(_Symbol);
   Backfill();
   EventSetTimer(60);
   SendHeartbeat();
   Print("CryptoNovaBridge: started (read-only, observation)");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); }

void OnTimer()
  {
   if(TimeLocal() - g_lastHb >= InpHeartbeatMin * 60) SendHeartbeat();
   else Flush();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   UpdateTz();
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.deal > 0)
     {
      if(!HistoryDealSelect(trans.deal)) return;
      long typ = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
      if(typ != DEAL_TYPE_BUY && typ != DEAL_TYPE_SELL) return;
      AddSym(HistoryDealGetString(trans.deal, DEAL_SYMBOL));
      Enqueue(DealJson(trans.deal, "deal", AccountInfoDouble(ACCOUNT_BALANCE), true));
      Flush();
     }
   else if(trans.type == TRADE_TRANSACTION_POSITION && trans.position > 0)    // SL / TP changed on an open position
     {
      Enqueue(StringFormat("{\"type\":\"modify\",\"pos\":%I64u,\"symbol\":\"%s\",\"sl\":%s,\"tp\":%s,\"t\":%I64d}",
                           trans.position, Esc(trans.symbol), D(trans.price_sl), D(trans.price_tp), UtcMs(TimeTradeServer())));
      Flush();
     }
  }
//+------------------------------------------------------------------+
