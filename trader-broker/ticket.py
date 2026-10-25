#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket plus the
            exact arguments Claude will send to Kite's place_order tool
            (Zerodha's official MCP server). An entry shows the stop-loss
            order that follows its fill, the target and the max loss at the
            stop with charges. An order is READY only if trader-risk passed
            THIS exact order today (matched by fingerprint) and nobody has
            decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened: placed (with Kite's order id and the
            fill), denied, or failed. In paper mode the fill is simulated at
            the price you give it. When an entry fills, it writes the
            stop-loss order (SL-M sell at your stop) as the next proposal.
  fill      records a fill in trader/portfolio.json (cash and shares).
  stoploss  writes the SL-M sell for a position you hold (if result did not).
  flatten   builds a market sell that closes a symbol's position: at the
            target, or by flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 2610190001 --filled-qty 36 --filled-price 1228.10
    python3 ticket.py stoploss --symbol RELIANCE --qty 36 --trigger 1215.30
    python3 ticket.py fill --symbol RELIANCE --side sell --qty 36 --price 1240.30
    python3 ticket.py flatten --symbol RELIANCE --qty 36
    python3 ticket.py log

Paper trading. Zerodha (and most Indian brokers) have no paper account, so
"paper": true (the default) means nothing goes to Kite at all: after your yes
the fill is simulated with result --filled-price <the latest quote>, and the
log and portfolio work exactly as they would live.

Everything is written to trader/state/broker.json (append only). Dates are
IST dates; --now "2026-10-19 09:50" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product", "order_type",
               "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "iceberg_legs",
               "iceberg_quantity", "tag")
NUMBER_FIELDS = ("quantity", "price", "trigger_price", "validity_ttl", "disclosed_quantity", "iceberg_legs",
                 "iceberg_quantity")
VALIDITY_WORDS = {"DAY": "good for today", "IOC": "immediate or cancel", "TTL": "good for some minutes"}
PRODUCT_WORDS = {"MIS": "MIS (intraday)", "CNC": "CNC (delivery)", "NRML": "NRML (F&O carry)", "MTF": "MTF (margin)"}
YES_WORDS = ("yes", "y", "haan", "han")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each", "sab", "sabhi"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if", "nahi", "ruko"}
TAG = "trdskill"
IST = datetime.timedelta(hours=5, minutes=30)


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST is UTC+5:30, no daylight saving)

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-10-19 09:50\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-19T09:50:00+05:30"."""
    return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date", else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _num(v):
    f = float(v)
    return str(int(f)) if f == int(f) else ("%.2f" % f)


def tool_input(order):
    """The exact arguments for Kite's place_order: its parameter names, numbers as numbers.
    "why", "plan" and "stop_loss_order" are ours and are never sent."""
    out = {}
    for k in KITE_FIELDS:
        if k in order and order[k] is not None:
            v = order[k]
            if k in NUMBER_FIELDS and _f(v) is not None:
                f = float(v)
                v = int(f) if f == int(f) and k != "price" and k != "trigger_price" else f
            out[k] = v
    return out


def _group(whole):
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + parts + [tail])


def _money(v):
    f = float(v)
    w, fr = ("%.2f" % abs(f)).split(".")
    s = _group(w) + ("" if fr == "00" else "." + fr)
    return ("-" if f < 0 else "") + "₹" + s


def _p(v):
    return "%.2f" % float(v)


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, charges included when the plan has them."""
    plan = order.get("plan") or {}
    sl = order.get("stop_loss_order") or {}
    stop = _f(sl.get("trigger_price")) or _f(plan.get("stop"))
    q = _f(order.get("quantity"))
    if not stop or not q:
        return None, stop
    if _f(plan.get("max_loss_with_charges")) is not None and str(order.get("order_type", "MARKET")) == "MARKET":
        return round(_f(plan["max_loss_with_charges"]), 2), stop
    if str(order.get("order_type", "MARKET")) in ("LIMIT", "SL") and _f(order.get("price")):
        entry = _f(order["price"])
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0 + (_f(plan.get("charges")) or 0), 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("transaction_type", "")).upper()
    sym = order.get("tradingsymbol", "?")
    q = _num(order.get("quantity") or 0)
    typ = str(order.get("order_type", "MARKET")).upper()
    tif = VALIDITY_WORDS.get(str(order.get("validity", "DAY")).upper(), order.get("validity"))
    product = PRODUCT_WORDS.get(str(order.get("product", "")).upper(), order.get("product"))
    if typ == "LIMIT":
        how = "limit %s" % _p(order.get("price"))
    elif typ == "SL-M":
        how = "SL-M (stop-loss market), trigger %s" % _p(order.get("trigger_price"))
    elif typ == "SL":
        how = "SL (stop-loss limit), trigger %s, limit %s" % (_p(order.get("trigger_price")), _p(order.get("price")))
    else:
        how = "market"
    parts = ["%s %s %s on %s, %s, %s, %s" % (side, q, sym, order.get("exchange", "NSE"), product, how, tif)]
    sl = order.get("stop_loss_order")
    plan = order.get("plan") or {}
    if sl:
        parts.append("stop-loss, the moment it fills: %s %s SL-M, trigger %s"
                     % (str(sl.get("transaction_type", "SELL")).upper(), _num(sl.get("quantity") or 0),
                        _p(sl.get("trigger_price"))))
    if plan.get("target") and side == "BUY":
        parts.append("target %s: watched by trader-alerts, taken with a sell ticket (flatten)" % _p(plan["target"]))
    loss, stop = max_loss(order)
    if loss is not None and side == "BUY":
        parts.append("max loss about %s if the stop fills at %s%s (it can fill worse in a fast market or a circuit)"
                     % (_money(loss), _p(stop), ", charges included" if plan.get("charges") else ""))
    return parts


def plain(order):
    """BUY 36 RELIANCE on NSE, MIS (intraday), market, good for today / stop-loss ... / target ... / max loss ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("quantity", "price", "trigger_price"):
        v = _f(order.get(k))
        if v is not None:
            nums.append(v)
    for k in ("entry", "stop", "target"):
        v = _f((order.get("plan") or {}).get(k))
        if v is not None:
            nums.append(v)
    v = _f((order.get("stop_loss_order") or {}).get("trigger_price"))
    if v is not None:
        nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" (or \"haan\") counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("tradingsymbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\-]{0,19}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the place_order call (or the paper fill)" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("transaction_type", "")).upper() == "BUY" else "exit"


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    paper = cfg.get("paper", True)
    acct = "PAPER (simulated: nothing is sent to Kite)" if paper else "LIVE ACCOUNT, REAL MONEY"
    lines = ["ORDER %d of %d, %s, Zerodha Kite" % (idx, total, acct)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if paper:
        lines.append("  Paper: after your yes the fill is simulated at the latest quote. Arguments it would send:")
    else:
        lines.append("  Claude will call place_order (Kite MCP) with exactly:")
    for l in json.dumps(tool_input(order), indent=2, ensure_ascii=False).splitlines():
        lines.append("    " + l)
    if ready:
        lines.append("  Reply \"yes\" to place this one order, or \"no\" to skip it.")
        if not paper:
            lines.append("  Claude's own card then asks again: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "zerodha"), "tool": "place_order",
            "tool_input": tool_input(order), "why": order.get("why", ""),
            "stop_loss_order": order.get("stop_loss_order"), "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "currency": "INR", "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log, so trader-risk can see what was bought and sold today."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
        "qty": _num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _num(float(qty)), symbol.upper(), _p(price))})
    write_json(state_path, state)


def proposal(path, order, cfg, now, source, note):
    """Write one order as the proposal in trader/orders.json. Returns how many it replaced."""
    old = read_json(path, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    write_json(path, {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "status": "proposed", "paper": bool(cfg.get("paper", True)), "currency": "INR",
                      "note": note, "source": source, "orders": [order]})
    return replaced


def stoploss_order(sym, qty, trigger, exchange="NSE", product="MIS"):
    return {"variety": "regular", "exchange": exchange, "tradingsymbol": sym, "transaction_type": "SELL",
            "quantity": int(qty), "product": product, "order_type": "SL-M", "trigger_price": round(float(trigger), 2),
            "validity": "DAY", "tag": TAG,
            "why": "stop-loss for the %s %s shares just bought: sells at market if the price trades at %s"
                   % (_num(qty), sym, _p(trigger))}


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("tradingsymbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    paper = bool(cfg.get("paper", True))
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(),
           "qty": order.get("quantity"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply, "paper": paper,
           "broker": cfg.get("broker", "zerodha")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if paper:
        print("PAPER: do NOT call place_order. Read the latest price (trader-alerts: alerts.py quote %s), then "
              "record the simulated fill:" % rec["symbol"])
        print("  ticket.py result --index %d --status placed --broker-id PAPER --filled-qty %s --filled-price P"
              % (a.index, _num(order.get("quantity") or 0)))
    else:
        print("Now call place_order (Kite MCP) with exactly:")
        print(json.dumps(tool_input(order), indent=2, ensure_ascii=False))
        print("Claude's card shows Allow once / Deny. Then get_order_history for the fill, and: ticket.py result "
              "--index %d --status placed|denied|failed" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status, "paper": rec.get("paper", True)}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s%s." % (a.index, a.status, " (paper)" if rec.get("paper", True) else ""))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
        sl = order.get("stop_loss_order")
        if rec.get("kind") == "entry" and rec["side"] == "buy" and sl:
            slo = stoploss_order(rec["symbol"], a.filled_qty, sl.get("trigger_price"), sl.get("exchange", "NSE"),
                                 sl.get("product", "MIS"))
            proposal(a.orders, slo, cfg, now, "ticket.py result: the stop-loss for order %d" % a.index,
                     "Stop-loss ticket. It is an exit: trader-risk never blocks it. Place it now.")
            print("PROTECT IT NOW: wrote the stop-loss order to %s: %s." % (a.orders, plain(slo)))
            print("  trader-risk (check.py), ticket.py show, the user's yes, then place_order. Until it is placed,"
                  " nothing at the broker stops a fall.")
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, a.symbol.upper(),
                                                                _money(a.price), date, a.portfolio,
                                                                _money(pf["cash"])))
    return 0


def _held(a, sym):
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or qty != int(qty):
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 36")
        return qty, "the broker, get_positions"
    pf = read_json(a.portfolio, "portfolio", required=False) or {}
    qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
              if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
    if qty <= 1e-9:
        raise TicketError("no %s shares in %s. Check the broker (get_positions) and pass --qty if you do hold "
                          "some" % (sym, a.portfolio))
    return qty, a.portfolio


def cmd_stoploss(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    qty, source = _held(a, sym)
    trigger = _f(a.trigger)
    if trigger is None:
        plan = read_json(a.plan, "plan", required=False) or {}
        if str(plan.get("symbol", "")).upper() == sym:
            trigger = _f(plan.get("stop"))
    if not trigger or trigger <= 0:
        raise TicketError("which stop? --trigger PRICE (or a trader/plan.json for %s)" % sym)
    order = stoploss_order(sym, qty, trigger, product=str(cfg.get("product") or "MIS").upper())
    replaced = proposal(a.orders, order, cfg, now, "ticket.py stoploss %s on %s" % (sym, today),
                        "Stop-loss ticket. It is an exit: trader-risk never blocks it. Place it now.")
    print("Stop-loss %s: %s (qty from %s)." % (sym, plain(order), source))
    print("Wrote it to %s%s. Next: check.py, ticket.py show, the user's yes, place_order."
          % (a.orders, (" (it replaced %d earlier proposal%s)" % (replaced, "" if replaced == 1 else "s"))
                       if replaced else ""))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    qty, source = _held(a, sym)
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or "15:10"
    order = {"variety": "regular", "exchange": str(cfg.get("exchange") or "NSE").upper(), "tradingsymbol": sym,
             "transaction_type": "SELL", "quantity": int(qty), "product": str(cfg.get("product") or "MIS").upper(),
             "order_type": "MARKET", "validity": "DAY", "tag": TAG,
             "why": "closes the %s day trade (at the target, or flat by %s IST; qty from %s)" % (sym, flat_by, source)}
    replaced = proposal(a.orders, order, cfg, now, "ticket.py flatten %s on %s" % (sym, today),
                        "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.")
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_positions before you say yes." % source)
    print("Next:")
    print("  1. CANCEL THE OPEN STOP-LOSS FIRST: get_orders, find the %s SL-M sell (status TRIGGER PENDING), then" % sym)
    print("     cancel_order (variety regular, its order_id; Needs approval). If it stays open after you sell, a")
    print("     later fall triggers it and opens a SHORT position in MIS. In paper mode: nothing to cancel.")
    print("  2. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  3. ticket.py show, then the user's yes for this one order, then place_order (or the paper fill).")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-19 09:50\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the yes")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    for name, helptext in (("stoploss", "an SL-M sell at your stop for a position you hold"),
                           ("flatten", "a market sell that closes a symbol's position")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--symbol", required=True)
        p.add_argument("--qty", help="shares you hold, from get_positions (default: trader/portfolio.json)")
        if name == "stoploss":
            p.add_argument("--trigger", help="the stop price (default: the stop in trader/plan.json)")
        p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--config", default="trader/config.json")
        clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "stoploss": cmd_stoploss, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
