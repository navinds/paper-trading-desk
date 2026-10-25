---
name: trader-broker
description: >-
  Order Executor for Zerodha (Kite). Sends approved orders to Zerodha
  through Kite's official MCP server, paper (simulated) by default, never
  without your yes. Shows each order as a plain ticket (an entry shows the
  SL-M stop-loss that follows its fill, the target, and the max loss at the
  stop with charges) plus the exact arguments for Kite's place_order,
  records your yes word for word, then Claude's own Allow once / Deny card
  asks again. After the entry fills it writes the stop-loss order; at the
  target or by 15:10 it builds the sell that closes the trade (cancel the
  stop-loss first). Records fills and logs each trade's max loss so
  trader-risk can hold the daily limit. Covers connecting Kite's MCP server
  to Claude Desktop and Claude Code, Kite's own login, and keeping order
  tools on approval. Use when the user says "place the order", "buy it",
  "execute", "send it to Zerodha", "Kite", "put the stop loss", "close my
  position", "square off", "exit", "connect Zerodha", "paper trade", or
  after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the exchange only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat ("yes"
   or "haan"; "ok", "sure", "yes to all" do not count).
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the Kite order tools need approval.

Two yeses from the user, one PASS from the rules. No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 36 --filled-price 1228.10
python3 "${CLAUDE_SKILL_DIR}/ticket.py" stoploss --symbol RELIANCE --qty 36 --trigger 1215.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 36 --price 1240.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 36
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the stop-loss and flatten tickets, and
keeps the log in `trader/state/broker.json`. `--now "2026-10-19 09:50"` pins
the clock (IST).

## Paper first: Zerodha has no paper account

Indian brokers do not offer paper accounts, so `"paper": true` (the default)
means nothing goes to Kite at all. After the user's yes, Claude reads the
latest price (`alerts.py quote SYMBOL`, or Kite's `get_ltp` if connected) and
records a simulated fill with `result ... --broker-id PAPER --filled-price P`.
The log, the portfolio, the stop-loss, the daily limits all work exactly as
they would live. Going live is the user's own edit of `trader/config.json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes the entry; `result`, `stoploss` and `flatten` replace it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the stop for `stoploss`, the flat-by time for `flatten` |

## Setup: Zerodha's Kite MCP server

Zerodha publishes an official MCP server: github.com/zerodha/kite-mcp-server,
hosted at `https://mcp.kite.trade/mcp`. Its tools include `login`,
`get_profile`, `get_margins`, `get_holdings`, `get_positions`, `get_quotes`,
`get_ltp`, `get_ohlc`, `get_historical_data`, `place_order`, `modify_order`,
`cancel_order`, `get_orders`, `get_trades`, `get_order_history` and the GTT
tools.

- **Claude Code:** `claude mcp add --transport http kite https://mcp.kite.trade/mcp`
- **Claude Desktop:** Settings > Developer > Edit Config, and add the block in
  `claude_desktop_config.example.json` here (it runs `npx mcp-remote
  https://mcp.kite.trade/mcp`; needs Node.js). Restart Claude Desktop.

**Logging in.** Ask Claude to call Kite's `login` tool. It returns a Zerodha
URL; the user opens it and logs in on Zerodha's own page (user ID, password,
TOTP). **Credentials never go in the chat.** Never ask for them, and if the
user pastes a password, PIN or TOTP anyway, tell them to delete that message
and change the password. Kite sessions end every day, so log in each morning.

**Order tools on approval.** In Claude Code, in `.claude/settings.json`:

```json
{"permissions": {"ask": ["mcp__kite__place_order", "mcp__kite__modify_order", "mcp__kite__cancel_order",
                         "mcp__kite__place_gtt_order", "mcp__kite__modify_gtt_order", "mcp__kite__delete_gtt_order"]}}
```

Never put an order tool in `allow`. In Claude Desktop: Customize > Connectors,
open kite, and set every tool that places, changes or cancels an order to
"Needs approval". Read-only tools (`get_*`) can be "Always allow".

Zerodha's README says the hosted server leaves out "potentially destructive
trading operations". If `place_order` is not in the tool list, orders need a
self-hosted server with your own Kite Connect app (see the repo); until then
the pack works in paper mode and Claude can still read positions and quotes.

## Kite has no bracket order for intraday

Zerodha retired bracket orders, and its GTT one-cancels-other does not take
MIS. So an intraday trade here is three orders, each with its own ticket and
its own yes:

1. **The entry**: BUY, MIS, MARKET (or LIMIT), from trader-budget.
2. **The stop-loss**, the moment the entry fills: SELL, MIS, SL-M, trigger at
   the plan's stop. `result` writes it for you. It is an exit: trader-risk
   never blocks it. Until it is placed, nothing at the broker stops a fall.
3. **The exit**: at the target (trader-alerts rings) or by 15:10, `flatten`
   writes a MARKET sell. **Cancel the open SL-M first** (`get_orders`, then
   `cancel_order` with variety regular and its order_id): an SL-M left open
   after you sell would trigger on a later fall and open a SHORT position.
   Zerodha squares off MIS itself from 15:25 and charges ₹50 + GST an order.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", ask again. Never turn a reply into a yes yourself.
4. Live: only after "Recorded your yes", call `place_order` with exactly the
   arguments the ticket printed. Claude's card appears: Allow once or Deny.
   Paper: do not call Kite; read the latest price.
5. Record it: `ticket.py result --index N --status placed --broker-id <id>
   --filled-qty Q --filled-price P` (the fill from `get_order_history` or
   `get_positions`), or `--status denied`, or `--status failed --note "..."`.
   A failed order is not retried without a new check and a new yes.
6. The stop-loss ticket is now in `trader/orders.json`: check.py, show, yes,
   `place_order`, `result`. Do it at once.

## Example: RELIANCE, Monday 19 October 2026, paper

```
ORDER 1 of 1, PAPER (simulated: nothing is sent to Kite), Zerodha Kite
  BUY 36 RELIANCE on NSE, MIS (intraday), market, good for today
  stop-loss, the moment it fills: SELL 36 SL-M, trigger 1215.30
  target 1240.30: watched by trader-alerts, taken with a sell ticket (flatten)
  max loss about ₹496.70 if the stop fills at 1215.30, charges included (it can fill worse in a fast market or a circuit)
  why: opening-range breakout on RELIANCE: 36 shares, bound by your ₹500 max loss per trade (...)
  READY: trader-risk PASS, waiting for your yes
  Paper: after your yes the fill is simulated at the latest quote. Arguments it would send:
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
     "quantity": 36, "product": "MIS", "order_type": "MARKET", "validity": "DAY", "tag": "trdskill"}
```

The user replies "yes". `approve` records it; the fill is simulated at
₹1,228.10 with `result`, which writes the SELL 36 SL-M trigger 1,215.30
ticket. `check.py` passes it as an exit, the user says yes, it is recorded.
The price never reaches the ₹1,240.30 target: the 12:00 bar trades down to
₹1,214.10, the SL-M triggers, and `ticket.py fill --symbol RELIANCE --side sell
--qty 36 --price 1215.30` closes the paper trade, about ₹496.70 down with
charges. That is the plan working: the loss was decided before the entry.
On a day the target rings instead, the exit is `flatten` (cancel the SL-M
first), the user's yes, then the sell.

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats passwords, PINs, TOTPs or API secrets.
- Never switches `"paper"` to false. The user does that themselves.
- Never places F&O, MTF or short orders, and never uses MIS leverage beyond the cash.
- Never promises a fill price: a market order and an SL-M fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
- SEBI's algo-trading rules for retail (2025) put broker-approved, exchange-tagged algos under the broker's
  control. This pack places no order on its own: every order is the user's, one yes at a time.
