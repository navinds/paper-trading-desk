---
name: trader-alerts
description: >-
  Price Alerts and the NSE clock. Rings when a price, a stop, a target or the
  opening bell hits. Turns the trade plan into three alerts in one go (the
  stop, the target and "flat by close" at 15:10 IST), plus above / below /
  stop / target levels, percent moves from the previous close, any time of
  day, and the NSE opening bell (9:15), each ringing once so you are not
  spammed. Knows NSE hours (pre-open 9:00, session 9:15 to 15:30) and the
  2026 NSE trading holidays, works out IST itself, fetches free delayed
  quotes (Yahoo, SYMBOL.NS) one symbol at a time, and writes
  trader/prices.json for sizing and the risk check. Use when the user says
  "alert me when", "tell me if Reliance hits", "watch my stop loss", "watch
  my trade", "remind me to square off by 3:10", "is the market open", "is
  today a holiday", "when does the market open", "get me a price", or right
  after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. The SL-M stop-loss order at Zerodha does the
stopping; the alerts tell the user what happened, ring at the target (there
is no bracket order for MIS, so the target is taken by hand with
trader-broker), and ring when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1215.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1240.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by close"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-19 15:10"` pins the clock (IST); `--offline DIR` reads saved
quote files (`yahoo-<SYMBOL>.json`) instead of the network. Symbols are NSE
symbols; `SYMBOL.BO` asks for the BSE price, `^NSEI` for the NIFTY 50 index.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today.

## Quotes

- Yahoo (`query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`), an
  unofficial free JSON endpoint, one request per symbol, half a second
  apart. On a 429 it waits and tries once more, then `query1`.
- Free quotes are unofficial and delayed. Fine for alerts and for checking a
  size, not for timing an order. Kite's price is the one that fills. Each
  price in `trader/prices.json` keeps its quote time, and trader-risk only
  uses one under 15 minutes old.
- If Yahoo is down and Kite is connected, read the price with `get_ltp` and
  write it into `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE": 1228.1}}`.

## The NSE clock

Pre-open 9:00 to 9:15 (orders collected 9:00 to 9:08), regular session 9:15
to 15:30 IST, closed on weekends and on NSE's 2026 trading holidays (NSE,
read 2026-10-25): Jan 15 (Maharashtra municipal elections), Jan 26, Mar 3
(Holi), Mar 26 (Ram Navami), Mar 31 (Mahavir Jayanti), Apr 3 (Good Friday),
Apr 14 (Ambedkar Jayanti), May 1 (Maharashtra Day), May 28 (Bakri Id), Jun 26
(Muharram), Sep 14 (Ganesh Chaturthi), Oct 2 (Gandhi Jayanti), Oct 20
(Dussehra), Nov 10 (Diwali Balipratipada), Nov 24 (Guru Nanak Jayanti), Dec 25.
Diwali Laxmi Pujan falls on Sunday 8 November 2026: NSE holds its one-hour
Muhurat session that evening, which this clock does not cover. IST has no
daylight saving, so the tool needs no time zone files. NSE publishes 2027's
list in December; until then the clock says 2027 is outside its calendar.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a scheduled
   task or `/loop 3m`. A check reads the latest delayed quote; it is not a
   live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop alert means the SL-M has
   probably filled: check `get_positions` and record the fill with
   trader-broker. A target alert or "flat by close" means take the exit:
   trader-broker `flatten` (cancel the SL-M first), then trader-risk, then the
   user's yes. Never act for them.

## Example: RELIANCE, Monday 19 October 2026

Entry ₹1,227.80 at 9:50 AM, stop ₹1,215.30, target ₹1,240.30, flat by 15:10.
`add --from-plan` writes `reliance-plan-stop`, `reliance-plan-target` and
`reliance-flat-by` for that day. A `check` at 10:55 reads RELIANCE at
₹1,221.10 (the 10:50 bar's close): between the stop and the target, nothing
rings. The 12:00 bar trades down to ₹1,214.10, and the next check rings:

```
ALERT RELIANCE is ₹1,214.80: at or below your stop ₹1215.30 (plan stop: opening-range midpoint).
```

The SL-M at Zerodha has done its job by then: check `get_positions`, record
the sell with `ticket.py fill`, and the trade is over: about ₹450 lost at the
stop plus ₹46.70 charges (an SL-M fills at market, so it can be a little
worse). The flat-by alert at 15:10 still rings once; with nothing open, it
needs nothing. On a day the trade is still open at 15:10 it reads:

```
ALERT Flat by close: it is 15:10 IST (set for 15:10), the market closes at 15:30. If RELIANCE is still
open, close it: trader-broker flatten --symbol RELIANCE builds the sell ticket (cancel the open stop-loss
first), then trader-risk and your yes. Zerodha squares off MIS itself from 15:25 and charges ₹50 + GST an
order for it.
```

The next morning the plan's alerts are no longer watched; with
`add --opening-bell`, the bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
