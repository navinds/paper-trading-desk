---
name: trader-budget
description: >-
  Budget Manager for NSE intraday trades in rupees. Sizes every position to
  your budget and your max loss per trade, Zerodha's charges included
  (brokerage, STT, exchange charges, SEBI fee, stamp duty, GST). Reads the
  trade plan from trader-exit (entry, stop, target), finds the whole shares
  that fit inside four limits (max loss per trade, position cap, max order,
  cash after your buffer), says which limit decided it, and writes ONE MIS
  entry order to trader/orders.json in the exact shape of Kite's
  place_order, with the SL-M stop-loss order that follows the fill. A stop
  too far away for your max loss is a plain refusal, not a smaller order.
  Nothing is placed. Use when the user says "how many shares", "quantity
  kitni", "size this trade", "I have 1 lakh", "I only want to risk 500 a
  trade", "set my rules", "keep 10% in cash", "what are the charges", or
  after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1,227.80, stop 1,215.30")
into an exact order ("buy 36 shares"), inside rules the user sets once. The
stop decides the size: the further away the stop, the fewer shares. And in
India the charges are part of the loss: on an intraday round trip they are
easily 10 % of a small stop. It writes a proposal only. trader-risk checks
it and trader-broker asks the user yes or no.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a LIMIT order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1227.50 --json # a LIMIT order at 1,227.50
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=30,TCS=25,HDFCBANK=25,INFY=20 --config split.json  # weights mode
```

`--now "2026-10-19 09:50"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed entry it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for, in rupees (example ₹1,00,000). Only
  money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at
  the stop, charges included (default ₹500, half a percent of ₹1 lakh).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest
  position (75% of the budget), the biggest order (₹80,000) and the smallest (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (₹1,500),
  **max_trades_per_day** (3), **min_reward_risk** (1.0), **require_stop**
  (true), **no_entries_before** / **no_entries_after** ("09:30" / "14:45")
  and **flat_by** ("15:10"), all IST.
- **include_charges** (true): size and check with the round-trip charges in
  the loss. **product**: "MIS" (intraday) or "CNC" (delivery).
- **blocked**: symbols they never want to trade.
- Keep **paper** true until they have watched it work on paper.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only. The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | the most shares whose (entry minus stop) x shares + round-trip charges fits `max_loss_per_trade_inr` |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The charges are Zerodha's equity schedule (zerodha.com/charges, read
2026-10-25). Intraday (MIS): brokerage 0.03% or ₹20 an executed order,
whichever is lower; STT 0.025% on the sell; NSE transaction charges 0.00307%;
SEBI ₹10 a crore; stamp duty 0.003% on the buy; GST 18% on brokerage +
transaction + SEBI. Delivery (CNC): no brokerage, STT 0.1% both sides, stamp
0.015%. For another broker, put its numbers under `"charges"` in the config.

The entry it sizes at: the limit price with `--limit`; for a market order the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. It refuses, and writes no order, when: the size is
zero shares, the order is under the minimum, reward:risk is under
`min_reward_risk`, the charges eat the whole target, the stop is not below
the entry, the target is not above it, the plan is for another day, the
symbol is blocked, or the plan is a short. Say the reason in one line.

## The order it writes

Kite has no bracket order for intraday, and Zerodha's GTT one-cancels-other
does not take MIS. So the file holds the entry, in Kite place_order
arguments, and next to it `stop_loss_order`: the SL-M sell at the stop that
trader-broker proposes the moment the entry fills. The target is watched by
trader-alerts and taken with a sell ticket.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees,
the loss at the stop, the charges line, and what the target makes after
charges. Say plainly that a stop is an order, not a promise: in a fast
market or a circuit it can fill below the stop. Then hand over to `/trader-risk`.

## Example: RELIANCE, Monday 19 October 2026

Entry ₹1,227.80 at 9:50 AM, stop ₹1,215.30, target ₹1,240.30 (1R). Risk
₹12.50 a share. The example config: ₹1,00,000 budget, 10% cash buffer, ₹500
max loss, charges included.

| limit | the math | shares |
| --- | --- | --- |
| max loss | ₹500 / 12.50 = 40, but 40 shares lose ₹500 + ₹51.87 charges | **36 (decides it)** |
| position cap | 75% of ₹1,00,000 = ₹75,000 / 1,227.80 | 61 |
| max order | ₹80,000 / 1,227.80 | 65 |
| cash | ₹1,00,000 minus ₹10,000 = ₹90,000 / 1,227.80 | 73 |

36 shares = ₹44,200.80. At the stop: 36 x 12.50 = ₹450, plus charges
(brokerage ₹26.39, STT ₹10.94, exchange ₹2.70, SEBI ₹0.09, stamp ₹1.33, GST
₹5.25 = ₹46.70) = ₹496.70. At the target the same 36 shares make ₹450 before
charges, ₹402.73 after. It writes:

```json
{"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
 "quantity": 36, "product": "MIS", "order_type": "MARKET", "validity": "DAY", "tag": "trdskill",
 "why": "opening-range breakout on RELIANCE: 36 shares, bound by your ₹500 max loss per trade (36 x 12.50 = ₹450 at the stop + ₹46.70 charges = ₹496.70)",
 "plan": {"entry": 1227.8, "stop": 1215.3, "target": 1240.3, "risk_per_share": 12.5, "max_loss": 450.0,
          "charges": 46.7, "max_loss_with_charges": 496.7, "setup": "orb", "entry_time": "9:50 AM", "flat_by": "15:10"},
 "stop_loss_order": {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL",
                     "quantity": 36, "product": "MIS", "order_type": "SL-M", "trigger_price": 1215.3, "validity": "DAY", "tag": "trdskill"}}
```

`why`, `plan` and `stop_loss_order` are notes for trader-risk and
trader-broker. They are never sent to Kite.

## Weights mode (kept from v1)

`--weights RELIANCE=30,TCS=25` or `--targets FILE` (`targets.example.json`
here) splits a budget across a list by weight, for delivery (CNC): the cash
buffer comes off the top, weights are renormalised to 100, any stock over
`max_position_pct` is pinned at the cap and its excess shared out, slices
under the minimum order are dropped. Whole shares only, so it needs prices
(`--prices trader/prices.json`, from `alerts.py quote RELIANCE TCS --write`).
These are plain buys with no stop: trader-risk blocks them while
`require_stop` is true. Use a separate config (`--config`) for a long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes F&O, MTF,
leveraged MIS or short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
- Taxes: intraday equity gains are speculative business income in India, taxed at your slab; nothing here is tax advice.
