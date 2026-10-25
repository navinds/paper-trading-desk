#!/usr/bin/env python3
"""Paper trading on the real NSE market with fake money, for trader-paper.
Standard library only. It never talks to a broker: fills are simulated at
live market prices, with slippage and Zerodha's real charges.

    python3 paper.py init --capital 10000            # a fresh ₹10,000 paper account + rules
    python3 paper.py run                             # the engine: 9:15 to 15:30 IST, every 30 seconds
    python3 paper.py run --replay 2026-10-19 --offline wall5m.json.gz --state /tmp/replay.json
    python3 paper.py status                          # cash, equity, open positions, today
    python3 paper.py trades                          # the trade journal
    python3 paper.py stats                           # win rate, average R, expectancy, drawdown
    python3 paper.py avoid RELIANCE --reason "results today"
    python3 paper.py close RELIANCE                  # exit at market on the next tick
    python3 paper.py note "Skipped banks: RBI policy at 10:00"

What the engine does, by itself, on every trading day (all rules, no opinions):
  09:00  start of day: reads NSE's board-meeting and corporate-action feeds
         and avoids any stock with a results board meeting or an ex-date
         today; adds your own avoid list (paper.py avoid).
  09:30  after every 5-minute bar: scans the universe (trader/universe.txt,
  -14:45 NIFTY 100 by default) for a FRESH opening-range breakout: the bar
         that just closed is the first close above the 9:15-9:30 high.
         Each trigger goes through, in order:
           1. trader-exit's rules: stop at the range midpoint, rounded down
              to the NSE tick, target at target_r (2R) times the risk;
           2. trader-timemachine: 2,000 replays from the stock's own 60
              days; a verdict leaning toward the stop is skipped;
           3. trader-budget: shares sized to max_loss_per_trade_inr with
              Zerodha's charges inside the loss; none affordable = skip;
           4. trader-risk: the same PASS / BLOCK gate as a real order.
         A PASS is bought at the live price plus slippage. Everything else
         is logged with its reason, so you see every call and every skip.
  always a position is checked on every poll: the stop (SL-M: a fall
         through it fills at the stop, a gap below it at the bar's open),
         then the target. Both inside one bar counts as the stop.
  15:10  anything still open is sold at the live price (flat_by).

Fills: a buy fills at the live price + slippage_pct (0.05%), a sell at the
live price - slippage_pct, rounded to the tick against you. Charges are
Zerodha's intraday schedule (trader-budget). If the engine was not running
at 15:10, the next start closes the position at its last known price and
adds Zerodha's ₹50 + 18% GST auto square-off charge, as Zerodha would.

Data: Yahoo Finance (SYMBOL.NS), free and unofficial. Its NSE quotes can
lag the exchange by up to about a minute. Bars are 5-minute bars for the
scan and 1-minute bars for open positions.

Files: trader/paper/state.json (the account, positions, trades, every
decision, the equity curve; the dashboard reads it), trader/paper/inbox.jsonl
(commands from paper.py avoid / close / note, picked up on the next tick).
"""

import argparse
import datetime
import gzip
import importlib.util
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(folder, name):
    """Load a tool from a sibling skill folder (the pack is installed side by side)."""
    path = os.path.join(HERE, "..", folder, name + ".py")
    if not os.path.isfile(path):
        sys.exit("paper.py: needs %s next to trader-paper (install the whole pack)" % os.path.normpath(path))
    spec = importlib.util.spec_from_file_location("paper_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _sibling("trader-scanner", "scan")
exitp = _sibling("trader-exit", "exit")
size = _sibling("trader-budget", "size")
risk = _sibling("trader-risk", "check")
tm = _sibling("trader-timemachine", "timemachine")
news = _sibling("trader-news", "news")

IST = datetime.timedelta(hours=5, minutes=30)
STATE = "trader/paper/state.json"
INBOX = "trader/paper/inbox.jsonl"
CONFIG = "trader/config.json"
PAPER_DEFAULTS = {
    "universe": "trader/universe.txt",
    "or_minutes": 15,
    "target_r": 2.0,
    "slippage_pct": 0.05,
    "poll_seconds": 30,
    "max_confirm": 30,
    "min_price": 50,
    "tm_gate": True,
    "tm_replays": 2000,
    "tm_skip": ["Leans toward the stop", "Strongly toward the stop"],
    "avoid_results_and_exdates": True,
}
SQUARE_OFF_CHARGE = 59.0   # Zerodha: ₹50 + 18% GST an order squared off by the broker


class PaperError(Exception):
    pass


# ---------------------------------------------------------------- small helpers

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def hhmm(text):
    h, m = str(text).split(":")
    return datetime.time(int(h), int(m))


def tick_of(price):
    return float(exitp.tick_for(price))


def to_tick(price, up):
    """Round a fill to the NSE tick, against you: up for a buy, down for a sell."""
    t = tick_of(price)
    n = price / t
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * t, 2)


def read_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rupees(x):
    return size.money(x)


def load_config(path=CONFIG):
    cfg = dict(size.DEFAULTS)
    cfg.update(PAPER_DEFAULTS)
    cfg.update(read_json(path, {}) or {})
    cfg["paper"] = True
    return cfg


def load_universe(path):
    out = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    s, _, n = ln.partition("|")
                    out[s.strip().upper()] = n.strip()
    return out


def new_state(capital):
    return {"created_at": iso(utc_now()), "mode": "paper", "currency": "INR",
            "account": {"start_capital": float(capital), "cash": float(capital), "realised": 0.0,
                        "charges": 0.0},
            "positions": [], "trades": [], "decisions": [], "notes": [], "equity": [],
            "days": {}, "broker_log": {"orders": []}, "engine": {"status": "stopped"},
            "watch": {}, "prices": {}}


# ---------------------------------------------------------------- market data

def bars_of(series, day, until_ist, step_min):
    """Closed bars of `day` on the step grid, from a parsed Yahoo series, oldest first."""
    out = {}
    h, l, o, v = series.get("h"), series.get("l"), series.get("o"), series.get("v")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        t = scan.ts_to_ist(ts)
        if t.date() != day or t.second or t.minute % step_min:
            continue
        if t.time() < scan.OPEN or t.time() >= scan.CLOSE:
            continue
        if t + datetime.timedelta(minutes=step_min) > until_ist:
            continue
        out[t] = {"start": t, "o": float(o[i] if o and o[i] is not None else c),
                  "h": float(h[i] if h and h[i] is not None else c), "l": float(l[i] if l and l[i] is not None else c),
                  "c": float(c), "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


class LiveData(object):
    """Yahoo Finance, live. One spark request per 20 symbols for the scan, one chart per position."""

    def __init__(self, pause=0.3):
        self.pause = pause
        self.note = "Yahoo Finance (free, unofficial; NSE quotes can lag up to about a minute)"

    def closes(self, syms, now):
        data, _ = scan.fetch_spark(list(syms), "1d", "5m", self.pause)
        return data

    def chart(self, sym, now, interval="5m"):
        """(series, last price) for today."""
        s = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", interval)), sym)
        return s, (s.get("meta") or {}).get("regularMarketPrice")

    def history60(self, sym):
        return tm.fetch_chart(sym)


class ReplayData(object):
    """A saved bundle {SYMBOL: v8 chart} replayed as if live: nothing after the clock is visible."""

    def __init__(self, bundle_path, history_dir=None):
        data, _ = scan.load_offline(bundle_path)
        self.data = data
        self.history_dir = history_dir
        self.note = "REPLAY of saved Yahoo bars (%s)" % os.path.basename(bundle_path)

    def closes(self, syms, now):
        return {s: self.data[s] for s in syms if s in self.data}

    def chart(self, sym, now, interval="5m"):
        s = self.data.get(sym)
        if not s:
            raise scan.ScanError("no data for %s in the replay bundle" % sym)
        local = to_ist(now)
        bars = bars_of(s, local.date(), local, 5)
        return s, (bars[-1]["c"] if bars else None)

    def history60(self, sym):
        if not self.history_dir:
            return None
        for name in ("%s-60d5m.json.gz" % sym, "%s-60d5m.json" % sym):
            p = os.path.join(self.history_dir, name)
            if os.path.exists(p):
                return tm.read_offline(p, sym)
        return None


# ---------------------------------------------------------------- the engine

class Engine(object):
    def __init__(self, cfg, data, state, emit=print, offline_news=None):
        self.cfg = cfg
        self.data = data
        self.st = state
        self.emit = emit
        self.offline_news = offline_news
        self.universe = load_universe(cfg["universe"])
        self.last_scan_slot = None

    # ------------------------------------------------ bookkeeping
    def log(self, now, symbol, action, reason, **extra):
        rec = dict({"at": to_ist(now).strftime("%Y-%m-%d %H:%M"), "symbol": symbol, "action": action,
                    "reason": reason}, **extra)
        self.st["decisions"].append(rec)
        self.emit("%s  %-11s %-9s %s" % (rec["at"][11:], symbol or "-", action.upper(), reason))

    def today(self, now):
        return to_ist(now).date().isoformat()

    def day(self, now):
        return self.st["days"].setdefault(self.today(now), {"avoid": {}, "trades": 0, "start_equity": None,
                                                            "loss_at_risk": 0.0})

    def equity(self):
        eq = self.st["account"]["cash"]
        for p in self.st["positions"]:
            eq += p["qty"] * (self.st["prices"].get(p["symbol"]) or p["entry"])
        return round(eq, 2)

    def portfolio(self):
        return {"cash": self.st["account"]["cash"],
                "positions": [{"ticker": p["symbol"], "shares": p["qty"],
                               "lots": [{"shares": p["qty"], "cost": p["qty"] * p["entry"]}]}
                              for p in self.st["positions"]]}

    # ------------------------------------------------ the day
    def start_day(self, now):
        d = self.day(now)
        if d.get("started"):
            return
        d["started"] = True
        for p in list(self.st["positions"]):
            if p["date"] < self.today(now):
                self.exit(p, self.st["prices"].get(p["symbol"]) or p["entry"], "auto square-off", now,
                          extra_charge=SQUARE_OFF_CHARGE,
                          why="the engine was not running at flat-by: closed at the last known price, with "
                              "Zerodha's ₹50 + GST auto square-off charge")
        d["start_equity"] = self.equity()
        if self.cfg.get("avoid_results_and_exdates"):
            self.avoid_from_nse(now)
        self.emit("Day %s: equity %s, %d symbols in the universe, %d avoided." % (
            self.today(now), rupees(d["start_equity"]), len(self.universe), len(d["avoid"])))

    def avoid_from_nse(self, now):
        """Results board meetings and ex-dates today: a rule, so the engine never trades into them."""
        d = self.day(now)
        today = to_ist(now).date()
        watch = dict(self.universe)
        fetcher = news.Fetcher(offline=self.offline_news)
        for feed, offline_name, kind in news.NSE_FEEDS[1:]:
            try:
                raw = news.parse_nse_feed(fetcher.get(news.NSE_RSS % feed, offline_name), kind)
            except news.NewsError as exc:
                self.log(now, "", "note", "NSE %s feed: %s (not used today)" % (kind, exc))
                continue
            for it in news.match_nse(raw, watch):
                text = it["title"] + " " + it["summary"]
                m = None
                for key in ("Meeting Date:", "Ex-Date:"):
                    if key in text:
                        m = text.split(key, 1)[1].strip()[:11]
                try:
                    when = datetime.datetime.strptime(m, "%d-%b-%Y").date() if m else None
                except ValueError:
                    when = None
                if when == today and (kind == "corporate action" or "result" in text.lower()):
                    why = "results board meeting today" if kind == "board meeting" else "ex-date today"
                    for sym in it["touches_nse"]:
                        d["avoid"][sym] = why
                        self.log(now, sym, "avoid", why + " (NSE filing)")

    # ------------------------------------------------ commands from paper.py avoid / close / note
    def read_inbox(self, now, path):
        if not path or not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        os.remove(path)
        for ln in lines:
            try:
                cmd = json.loads(ln)
            except ValueError:
                continue
            sym = str(cmd.get("symbol") or "").upper()
            if cmd.get("cmd") == "avoid":
                self.day(now)["avoid"][sym] = cmd.get("reason") or "avoided by you"
                self.log(now, sym, "avoid", cmd.get("reason") or "avoided by you")
            elif cmd.get("cmd") == "unavoid":
                self.day(now)["avoid"].pop(sym, None)
                self.log(now, sym, "note", "back in the universe")
            elif cmd.get("cmd") == "close":
                for p in self.st["positions"]:
                    if p["symbol"] == sym:
                        p["close_requested"] = True
            elif cmd.get("cmd") == "note":
                self.st["notes"].append({"at": to_ist(now).strftime("%Y-%m-%d %H:%M"), "text": cmd.get("text", "")})

    # ------------------------------------------------ positions
    def manage(self, now):
        local = to_ist(now)
        flat = datetime.datetime.combine(local.date(), hhmm(self.cfg["flat_by"]))
        for p in list(self.st["positions"]):
            try:
                series, last = self.data.chart(p["symbol"], now, "1m" if isinstance(self.data, LiveData) else "5m")
            except (scan.ScanError, OSError) as exc:
                self.log(now, p["symbol"], "note", "could not read the price: %s" % exc)
                continue
            step = 1 if isinstance(self.data, LiveData) else 5
            checked = datetime.datetime.strptime(p["checked_to"], "%Y-%m-%d %H:%M")
            bars = [b for b in bars_of(series, local.date(), local, step) if b["start"] >= checked]
            done = False
            for b in bars:
                p["checked_to"] = (b["start"] + datetime.timedelta(minutes=step)).strftime("%Y-%m-%d %H:%M")
                if b["l"] <= p["stop"]:
                    px = min(p["stop"], b["o"])
                    self.exit(p, px, "stop", now, at=b["start"])
                    done = True
                    break
                if b["h"] >= p["target"]:
                    px = max(p["target"], b["o"])
                    self.exit(p, px, "target", now, at=b["start"])
                    done = True
                    break
            if done:
                continue
            if last:
                self.st["prices"][p["symbol"]] = float(last)
            if p.get("close_requested"):
                self.exit(p, float(last or p["entry"]), "closed by you", now)
            elif local >= flat:
                self.exit(p, float(last or p["entry"]), "flat by %s" % self.cfg["flat_by"], now)

    def exit(self, p, price, reason, now, at=None, extra_charge=0.0, why=None):
        slip = self.cfg["slippage_pct"] / 100.0
        fill = to_tick(price * (1 - slip), up=False)
        buy_v, sell_v = p["qty"] * p["entry"], p["qty"] * fill
        ch = size.charges(buy_v, sell_v, "MIS", self.cfg.get("charges"))
        charges = round(ch["total"] + extra_charge, 2)
        pnl = round(sell_v - buy_v - charges, 2)
        risk_rs = p["qty"] * (p["entry"] - p["stop"])
        acct = self.st["account"]
        acct["cash"] = round(acct["cash"] + sell_v - charges, 2)
        acct["realised"] = round(acct["realised"] + pnl, 2)
        acct["charges"] = round(acct["charges"] + charges, 2)
        when = to_ist(now) if at is None else at + datetime.timedelta(minutes=1 if isinstance(self.data, LiveData) else 5)
        trade = dict(p, exit=fill, exit_time=when.strftime("%H:%M"), exit_reason=reason, charges=charges,
                     gross=round(sell_v - buy_v, 2), pnl=pnl, r=round(pnl / risk_rs, 2) if risk_rs else None,
                     charges_detail=ch)
        for k in ("checked_to", "close_requested"):
            trade.pop(k, None)
        self.st["trades"].append(trade)
        self.st["positions"].remove(p)
        self.st["prices"][p["symbol"]] = fill
        self.st["broker_log"]["orders"].append({"status": "placed", "date": p["date"], "symbol": p["symbol"],
                                                "side": "sell", "kind": "exit", "qty": p["qty"]})
        self.log(now, p["symbol"], "exit", "%s: sold %d at %s, P&L %s after %s charges (%+.2fR)%s" % (
            reason, p["qty"], "%.2f" % fill, rupees(pnl), rupees(charges), trade["r"] or 0,
            (". " + why) if why else ""), pnl=pnl)

    # ------------------------------------------------ the scan and the calls
    def scan_slot(self, now):
        """The 5-minute bar that has just closed (its index), or None."""
        local = to_ist(now)
        open_dt = datetime.datetime.combine(local.date(), scan.OPEN)
        mins = int((local - open_dt).total_seconds() // 60)
        if mins < 0:
            return None
        return mins // 5 - 1

    def scan(self, now):
        cfg, local = self.cfg, to_ist(now)
        d = self.day(now)
        before = datetime.datetime.combine(local.date(), hhmm(cfg["no_entries_before"]))
        after = datetime.datetime.combine(local.date(), hhmm(cfg["no_entries_after"]))
        slot = self.scan_slot(now)
        n_or = cfg["or_minutes"] // 5
        if slot is None or slot < n_or or slot == self.last_scan_slot:
            return
        self.last_scan_slot = slot
        if local < before or local > after + datetime.timedelta(minutes=5):
            return
        if d["trades"] >= int(cfg.get("max_trades_per_day") or 99):
            return
        held = {p["symbol"] for p in self.st["positions"]}
        traded = {t["symbol"] for t in self.st["trades"] if t["date"] == self.today(now)}
        syms = [s for s in sorted(self.universe) if s not in held and s not in traded and s not in d["avoid"]]
        cutoff = datetime.datetime.combine(local.date(), scan.OPEN) + datetime.timedelta(minutes=5 * (slot + 1))
        try:
            stage1 = self.data.closes(syms, now)
        except (scan.ScanError, OSError) as exc:
            self.log(now, "", "note", "scan failed: %s" % exc)
            return
        cands = []
        for s in syms:
            series = stage1.get(s)
            if not series:
                continue
            bars = scan.session_bars(series, local.date(), cutoff)
            if not scan.usable(bars, local.date(), cutoff, n_or) or bars[-1]["c"] < cfg["min_price"]:
                continue
            top = max(b["c"] for b in bars[:n_or])
            # fresh: the bar that just closed is at or above the range's closes, and none before it was
            if bars[-1]["c"] >= top and not any(b["c"] >= top for b in bars[n_or:-1]):
                cands.append((s, sum(b["v"] for b in bars) * bars[-1]["c"]))
        cands.sort(key=lambda x: -x[1])
        watch = self.st["watch"].setdefault(self.today(now), {})
        for s, _ in cands[:cfg["max_confirm"]]:
            try:
                series, last = self.data.chart(s, now, "5m")
            except (scan.ScanError, OSError) as exc:
                self.log(now, s, "skip", "could not read the chart: %s" % exc)
                continue
            full = scan.session_bars(series, local.date(), cutoff)
            if not scan.usable(full, local.date(), cutoff, n_or):
                continue
            hit = scan.orb_hit(full, n_or)
            if not hit:
                continue
            watch[s] = {"or_high": hit["or_high"], "or_low": hit["or_low"], "trigger": hit["price"],
                        "at": hit["at"], "status": "triggered"}
            if hit["bar_index"] != len(full) - 1:
                watch[s]["status"] = "late"
                continue
            if local > after:
                watch[s]["status"] = "skipped"
                watch[s]["reason"] = "after %s, no new entries" % cfg["no_entries_after"]
                self.log(now, s, "skip", watch[s]["reason"])
                continue
            self.consider(s, hit, last, now, watch[s])
            if d["trades"] >= int(cfg.get("max_trades_per_day") or 99):
                break

    def consider(self, sym, hit, last, now, w):
        cfg, today = self.cfg, self.today(now)
        price = hit["price"]
        try:
            c = exitp.compute(price, "or_mid", None, "r", exitp.D(cfg["target_r"]), hit["or_high"], hit["or_low"])
        except exitp.PlanError as exc:
            return self.skip(now, sym, w, "plan: %s" % exc)
        plan = {"symbol": sym, "side": "buy", "date": today, "setup": "orb", "entry": float(c["entry"]),
                "entry_time": hit["at"], "stop": float(c["stop"]), "target": float(c["target"]),
                "stop_label": c["stop_label"], "target_label": c["target_label"], "flat_by": cfg["flat_by"],
                "opening_range": {"high": hit["or_high"], "low": hit["or_low"], "minutes": cfg["or_minutes"]},
                "stop_rule": "or_mid", "interval": "5m"}
        verdict = None
        if cfg.get("tm_gate"):
            try:
                text = self.data.history60(sym)
                if text:
                    doc = tm.time_machine(text, tm.normalize_plan(plan), now, replays=int(cfg["tm_replays"]),
                                          until=cfg["flat_by"])
                    verdict = doc["verdict"]
                    w["verdict"] = verdict
                    w["odds"] = {"target": doc["mc"]["p_target"], "stop": doc["mc"]["p_stop"],
                                 "neither": doc["mc"]["p_neither"]}
                    if any(verdict.startswith(v) for v in cfg["tm_skip"]):
                        return self.skip(now, sym, w, "time machine: %s" % verdict)
                else:
                    w["verdict"] = "no 60-day history available: not replayed"
            except (tm.TMError, OSError) as exc:
                w["verdict"] = "time machine could not run: %s" % exc
        try:
            s = size.size_trade(plan, cfg, today=today, cash=self.st["account"]["cash"])
        except size.SizeError as exc:
            return self.skip(now, sym, w, "size: %s" % exc)
        order = size.entry_order(s)
        if order is None:
            return self.skip(now, sym, w, "size: %s" % s["refusal"])
        res = risk.check([order], cfg, self.portfolio(), {sym: float(last or price)}, self.st["broker_log"],
                         today, now, None)
        r = res["results"][0]
        if r["verdict"] != "PASS":
            return self.skip(now, sym, w, "risk: " + "; ".join(r["reasons"]))
        self.enter(sym, plan, s, order, float(last or price), now, w, verdict)

    def skip(self, now, sym, w, reason):
        w["status"] = "skipped"
        w["reason"] = reason
        self.log(now, sym, "skip", reason)

    def enter(self, sym, plan, s, order, last, now, w, verdict):
        slip = self.cfg["slippage_pct"] / 100.0
        fill = to_tick(last * (1 + slip), up=True)
        qty = int(order["quantity"])
        cost = round(qty * fill, 2)
        if cost > self.st["account"]["cash"] + 0.005:
            return self.skip(now, sym, w, "the fill at %.2f costs %s, more than the cash" % (fill, rupees(cost)))
        self.st["account"]["cash"] = round(self.st["account"]["cash"] - cost, 2)
        local = to_ist(now)
        pos = {"symbol": sym, "date": self.today(now), "setup": "orb", "qty": qty, "entry": fill,
               "signal_price": plan["entry"], "entry_time": local.strftime("%H:%M"), "stop": plan["stop"],
               "target": plan["target"], "risk_per_share": round(fill - plan["stop"], 2),
               "planned_max_loss": s["max_loss_with_charges"], "or_high": plan["opening_range"]["high"],
               "or_low": plan["opening_range"]["low"], "verdict": verdict, "bound_by": s["bound_by"],
               "why": order["why"], "checked_to": local.strftime("%Y-%m-%d %H:%M")}
        self.st["positions"].append(pos)
        self.st["prices"][sym] = last
        d = self.day(now)
        d["trades"] += 1
        d["loss_at_risk"] = round(d["loss_at_risk"] + s["max_loss_with_charges"], 2)
        self.st["broker_log"]["orders"].append({"status": "placed", "date": pos["date"], "symbol": sym,
                                                "side": "buy", "kind": "entry", "qty": qty,
                                                "max_loss": s["max_loss_with_charges"]})
        w["status"] = "entered"
        self.log(now, sym, "enter", "bought %d at %.2f (signal %.2f), stop %.2f, target %.2f, max loss %s with "
                 "charges; %s" % (qty, fill, plan["entry"], plan["stop"], plan["target"],
                                 rupees(s["max_loss_with_charges"]), verdict or "no replay"),
                 qty=qty, price=fill)

    # ------------------------------------------------ one tick
    def tick(self, now, inbox=None):
        local = to_ist(now)
        st = self.st
        st["engine"].update({"status": "running", "last_tick": iso(now), "now_ist": local.strftime("%Y-%m-%d %H:%M:%S"),
                             "data": self.data.note})
        self.read_inbox(now, inbox)
        if not scan.trading_day(local.date())[0]:
            st["engine"]["market"] = "closed: %s" % scan.trading_day(local.date())[1]
            return
        if local.time() < datetime.time(9, 0):
            st["engine"]["market"] = "before the pre-open"
            return
        self.start_day(now)
        if local.time() < scan.OPEN:
            st["engine"]["market"] = "pre-open"
            return
        st["engine"]["market"] = "open" if local.time() < scan.CLOSE else "closed for the day"
        if self.st["positions"]:
            self.manage(now)
        if local.time() < scan.CLOSE:
            self.scan(now)
        if local.time() <= datetime.time(15, 31):
            st["equity"].append({"t": local.strftime("%Y-%m-%d %H:%M"), "equity": self.equity()})
            if len(st["equity"]) > 20000:
                st["equity"] = st["equity"][-20000:]
        st["stats"] = stats(st)


# ---------------------------------------------------------------- statistics

def stats(st):
    trades = st["trades"]
    acct = st["account"]
    eq_now = acct["cash"] + sum(p["qty"] * (st["prices"].get(p["symbol"]) or p["entry"]) for p in st["positions"])
    out = {"trades": len(trades), "equity": round(eq_now, 2), "start_capital": acct["start_capital"],
           "total_pnl": round(eq_now - acct["start_capital"], 2), "charges": acct["charges"],
           "realised": acct["realised"]}
    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        rs = [t["r"] for t in trades if t["r"] is not None]
        out.update({
            "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(trades), 3),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "expectancy": round(sum(t["pnl"] for t in trades) / len(trades), 2),
            "gross": round(sum(t["gross"] for t in trades), 2),
            "profit_factor": (round(sum(t["pnl"] for t in wins) / -sum(t["pnl"] for t in losses), 2)
                              if losses and sum(t["pnl"] for t in losses) < 0 else None),
            "best": max(trades, key=lambda t: t["pnl"])["pnl"], "worst": min(trades, key=lambda t: t["pnl"])["pnl"],
        })
        by = {}
        for t in trades:
            k = t["exit_reason"].split(":")[0].split(" by ")[0]
            b = by.setdefault(k, {"n": 0, "pnl": 0.0})
            b["n"] += 1
            b["pnl"] = round(b["pnl"] + t["pnl"], 2)
        out["by_exit"] = by
        hours = {}
        for t in trades:
            h = t["entry_time"][:2] + ":00"
            b = hours.setdefault(h, {"n": 0, "pnl": 0.0})
            b["n"] += 1
            b["pnl"] = round(b["pnl"] + t["pnl"], 2)
        out["by_hour"] = dict(sorted(hours.items()))
    daily = {}
    for t in trades:
        daily[t["date"]] = round(daily.get(t["date"], 0.0) + t["pnl"], 2)
    out["daily"] = [{"date": k, "pnl": v} for k, v in sorted(daily.items())]
    peak, dd = None, 0.0
    for p in st["equity"]:
        peak = p["equity"] if peak is None else max(peak, p["equity"])
        dd = max(dd, peak - p["equity"])
    out["max_drawdown"] = round(dd, 2)
    return out


# ---------------------------------------------------------------- commands

def load_state(path):
    st = read_json(path)
    if st is None:
        raise PaperError("no paper account at %s: run paper.py init --capital 10000 first" % path)
    return st


def cmd_init(a):
    if os.path.exists(a.state) and not a.force:
        raise PaperError("%s already exists (%d trades). Use --force to start over." % (
            a.state, len(load_state(a.state)["trades"])))
    cap = float(a.capital)
    if cap < 1000:
        raise PaperError("start with at least ₹1,000")
    write_json(a.state, new_state(cap))
    cfg = read_json(a.config, None)
    if cfg is None or a.force:
        cfg = {"budget": cap, "cash_buffer_pct": 5, "max_position_pct": 90, "max_order_inr": cap,
               "min_order_inr": 500, "max_loss_per_trade_inr": round(cap * 0.01, 2),
               "max_daily_loss_inr": round(cap * 0.03, 2), "max_trades_per_day": 3, "max_orders_per_day": 10,
               "min_reward_risk": 1.0, "require_stop": True, "include_charges": True,
               "no_entries_before": "09:30", "no_entries_after": "14:45", "flat_by": "15:10", "product": "MIS",
               "exchange": "NSE", "broker": "zerodha", "paper": True, "blocked": [], "allow_fno": False,
               "allow_margin": False, "allow_short": False}
        cfg.update({k: v for k, v in PAPER_DEFAULTS.items()})
        write_json(a.config, cfg)
        print("Wrote the rules to %s: max loss %s a trade (1%%), %s a day (3%%), up to 3 trades a day, flat by 15:10."
              % (a.config, rupees(cfg["max_loss_per_trade_inr"]), rupees(cfg["max_daily_loss_inr"])))
    print("Paper account ready: %s of fake money in %s. Real NSE prices, simulated fills, Zerodha's charges." % (
        rupees(cap), a.state))
    if not os.path.exists(load_config(a.config)["universe"]):
        print("Next: python3 trader-scanner/scan.py universe --index nifty100 (the stocks the engine scans).")
    return 0


def cmd_run(a):
    cfg = load_config(a.config)
    if not os.path.exists(cfg["universe"]):
        raise PaperError("no universe at %s: run scan.py universe --index nifty100 first" % cfg["universe"])
    st = load_state(a.state)
    until = hhmm(a.until)
    if a.replay:
        if not a.offline:
            raise PaperError("--replay needs --offline BUNDLE (saved Yahoo 5-minute charts)")
        data = ReplayData(a.offline, a.history)
        eng = Engine(cfg, data, st, offline_news=a.news)
        st["mode"] = "replay"
        day = datetime.date.fromisoformat(a.replay)
        t = datetime.datetime.combine(day, datetime.time(9, 0))
        end = datetime.datetime.combine(day, until)
        while t <= end:
            eng.tick(ist_to_utc(t), a.inbox)
            t += datetime.timedelta(minutes=5)
            if t.time() > datetime.time(9, 15):
                t = t.replace(second=30)
        st["engine"]["status"] = "stopped"
        write_json(a.state, st)
        print_status(st)
        return 0
    eng = Engine(cfg, LiveData(), st)
    ok, why = scan.trading_day(to_ist(utc_now()).date())
    if not ok:
        print("No session today (%s): nothing to do." % why)
        return 0
    if to_ist(utc_now()).time() >= until:
        raise PaperError("it is past %s IST: the engine runs on trading days from before 9:15 to %s. Start it "
                         "tomorrow morning (start.sh), or replay a day with --replay." % (a.until, a.until))
    print("Paper engine running: %s, polling every %ss until %s IST. Ctrl-C stops it; the dashboard keeps the state."
          % (eng.data.note, cfg["poll_seconds"], a.until), flush=True)
    try:
        while True:
            now = utc_now()
            try:
                eng.tick(now, a.inbox)
            except (scan.ScanError, OSError) as exc:
                eng.log(now, "", "note", "tick failed: %s (trying again next poll)" % exc)
            write_json(a.state, st)
            if to_ist(now).time() >= until:
                break
            time.sleep(float(cfg["poll_seconds"]))
    except KeyboardInterrupt:
        print("Stopped.")
    st["engine"]["status"] = "stopped"
    write_json(a.state, st)
    return 0


def print_status(st):
    s = stats(st)
    acct = st["account"]
    print("Paper account (%s): equity %s, cash %s, total P&L %s (%+.2f%%), charges paid %s, %d trades." % (
        st.get("mode", "paper"), rupees(s["equity"]), rupees(acct["cash"]), rupees(s["total_pnl"]),
        100.0 * s["total_pnl"] / acct["start_capital"], rupees(acct["charges"]), s["trades"]))
    for p in st["positions"]:
        last = st["prices"].get(p["symbol"]) or p["entry"]
        print("  OPEN %-10s %d @ %.2f, last %.2f, stop %.2f, target %.2f, unrealised %s" % (
            p["symbol"], p["qty"], p["entry"], last, p["stop"], p["target"], rupees(p["qty"] * (last - p["entry"]))))
    eng = st.get("engine", {})
    if eng.get("now_ist"):
        print("Engine: %s, last tick %s IST, market %s. Data: %s." % (eng.get("status"), eng.get("now_ist"),
                                                                     eng.get("market", "?"), eng.get("data", "?")))


def cmd_status(a):
    print_status(load_state(a.state))
    return 0


def cmd_trades(a):
    st = load_state(a.state)
    rows = [t for t in st["trades"] if not a.date or t["date"] == a.date]
    if not rows:
        print("No trades yet.")
        return 1
    for t in rows:
        print("%s %s-%s %-10s %3d @ %8.2f -> %8.2f  %-22s P&L %10s  charges %7s  %+.2fR" % (
            t["date"], t["entry_time"], t["exit_time"], t["symbol"], t["qty"], t["entry"], t["exit"],
            t["exit_reason"][:22], rupees(t["pnl"]), rupees(t["charges"]), t["r"] or 0))
    return 0


def cmd_stats(a):
    s = stats(load_state(a.state))
    print(json.dumps(s, indent=2, ensure_ascii=False) if a.json else "\n".join(
        "%-14s %s" % (k, v) for k, v in s.items() if not isinstance(v, (list, dict))))
    return 0


def cmd_inbox(a, doc):
    d = os.path.dirname(a.inbox)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.inbox, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print("Queued for the engine's next tick: %s" % json.dumps(doc, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper trading on the real NSE market with fake money (trader-paper).")
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--inbox", default=INBOX)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("init", help="a fresh paper account")
    p.add_argument("--capital", required=True, help="fake rupees to start with, e.g. 10000")
    p.add_argument("--force", action="store_true", help="start over: wipes the paper account and the rules")
    p = sub.add_parser("run", help="the engine, 9:15 to 15:30 IST")
    p.add_argument("--until", default="15:35", help="stop at this IST time (default 15:35)")
    p.add_argument("--replay", metavar="DATE", help="replay a past day from --offline data instead of live")
    p.add_argument("--offline", metavar="BUNDLE", help="saved Yahoo 5-minute charts {SYMBOL: v8 chart}")
    p.add_argument("--history", metavar="DIR", help="replay: folder of <SYMBOL>-60d5m.json(.gz) for the time machine")
    p.add_argument("--news", metavar="DIR", help="replay: folder of saved NSE feeds")
    sub.add_parser("status", help="the account right now")
    p = sub.add_parser("trades", help="the trade journal")
    p.add_argument("--date")
    p = sub.add_parser("stats", help="the statistics")
    p.add_argument("--json", action="store_true")
    for name in ("avoid", "unavoid", "close"):
        p = sub.add_parser(name)
        p.add_argument("symbol")
        p.add_argument("--reason", default="")
    p = sub.add_parser("note", help="a note on today's journal (Claude's reasoning, or yours)")
    p.add_argument("text")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            return cmd_init(a)
        if a.cmd == "run":
            return cmd_run(a)
        if a.cmd == "status":
            return cmd_status(a)
        if a.cmd == "trades":
            return cmd_trades(a)
        if a.cmd == "stats":
            return cmd_stats(a)
        if a.cmd in ("avoid", "unavoid", "close"):
            return cmd_inbox(a, {"cmd": a.cmd, "symbol": a.symbol.upper(), "reason": a.reason})
        if a.cmd == "note":
            return cmd_inbox(a, {"cmd": "note", "text": a.text})
        ap.print_help()
        return 1
    except PaperError as e:
        print("paper.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
