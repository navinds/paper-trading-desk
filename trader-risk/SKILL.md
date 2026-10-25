---
name: trader-risk
description: >-
  Risk Manager for NSE trades. Checks every order against your limits and
  stops anything over them, before it can reach the broker. For an intraday
  trade: a stop is required (with its SL-M stop-loss order), the loss at
  the stop plus Zerodha's round-trip charges must fit your max loss per
  trade (measured from the higher of the plan's entry and a fresh price),
  reward:risk must meet your minimum, the day's total risk must fit your
  daily loss limit, no more than your trades per day, and no new entries
  outside your entry window (9:30 to 14:45 IST) or when the NSE is closed.
  It also keeps the basics: blocked symbols, order size, position cap, cash
  buffer, margin (MTF, MIS leverage), short sales, F&O and commodities,
  malformed Kite orders, duplicates and a daily order limit. Getting out
  (the stop-loss, the target sell, the flatten) is never blocked by a day
  limit. Prints PASS or BLOCK with the reason and records the verdicts so
  trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my
  rules", "can I take this trade", "how much have I risked today", or
  before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker (the stop-loss, the flatten)
passes through it. A BLOCK is final for that order: it does not go to the
user for a yes, and it does not go to Kite. SEBI found 7 in 10 individual
intraday traders in the cash segment lost money. These limits keep each loss
and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 09:50"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders, Kite place_order shape (trader-budget, or trader-broker's stoploss and flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold: the SL-M stop-loss
after a fill, the sell at the target, the flatten by 15:10. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled.

| check | blocks when |
| --- | --- |
| format | no tradingsymbol, transaction_type not BUY or SELL, quantity not a whole number; an unknown variety, product, order_type or validity; iceberg or auction varieties; LIMIT without a price, SL without price and trigger_price, SL-M without trigger_price |
| asset | NFO, BFO, MCX, CDS or an NRML product while `allow_fno` is false; anything that is not an NSE or BSE equity symbol; MTF while `allow_margin` is false |
| short | a sell of more than you hold (`allow_short` false; MIS lets you short, this pack does not) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin. MIS gives up to 5x leverage at Zerodha; this pack pays in full unless `allow_margin` is true |
| stop | (entries) no stop while `require_stop` is true (the plan's stop and its `stop_loss_order`); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop), plus the round-trip charges when `include_charges` is true, over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the NSE is closed (weekend, NSE holiday, outside 9:15 to 15:30) |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a LIMIT order. For a
  MARKET order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old).
- **Charges** are Zerodha's equity schedule, the same table as
  trader-budget: an intraday round trip on ₹44,000 costs about ₹47.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size trades use up a ₹1,500 day.
- **The window** is IST, worked out in the tool, never from the computer's
  clock zone. Set both `no_entries_*` to null to switch the window off.

Warnings do not block, but say them to the user: a LIVE account, a price
sent as text instead of a number, a stop that lives only in the plan with no
stop-loss order, a price too old to use, an after-market (amo) order.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop with charges, reward:risk, and how much of today's limits it uses.
   For a BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again: trader-broker
   matches each order to its verdict by fingerprint.

## Example: RELIANCE, Monday 19 October 2026

The entry from trader-budget at 9:50 AM, on a fresh ₹1,00,000 paper account:

```
PASS  #1 buy RELIANCE 36 shares, entry
          entry 1227.80 (the plan's entry), stop 1215.30, target 1240.30: risk 12.50 a share
          max loss ₹496.70 with ₹46.70 charges (limit ₹500), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss
          MIS (intraday): out by your flat-by 15:10; Zerodha squares off from 15:25

1 passed, 0 blocked at 2026-10-19 09:50 IST. Cash after the passed orders: ₹55,799.20 (paper account).
```

40 shares instead is blocked: `loss: 40 shares x 12.50 risk a share + ₹51.87
charges = ₹551.87 at the stop, over your ₹500 max loss per trade`. At 9:25
the same 36 shares are blocked by the window (no new entries before 9:30).
The SL-M stop-loss after the fill and a flatten sell later in the day both
pass: they are exits, and getting out is never blocked.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; Kite's price is the one that fills, and a
  stop can fill worse than its trigger in a fast market or a circuit.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms. SEBI's study: 9 in 10 individual F&O traders lost money.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
