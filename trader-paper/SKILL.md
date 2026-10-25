---
name: trader-paper
description: >-
  Paper Trading Desk: trade the real NSE market, live, with fake money. A
  local engine watches live prices from 9:15 to 15:30 IST and trades by
  itself with the pack's own rules: it scans the NIFTY 100 for fresh
  opening-range breakouts, plans each one (stop at the range midpoint,
  rounded to the NSE tick, 2R target), replays it in the time machine,
  sizes it to your max loss with Zerodha's charges, runs the same risk
  check a real order gets, then simulates the fill at the live price with
  slippage and charges, manages the stop and target, and closes everything
  by 15:10. It avoids stocks with results or an ex-date today, logs every
  call and every skip with its reason, and serves a live dashboard
  (equity curve, P&L, open positions, today's calls, journal, win rate,
  average R, expectancy, drawdown, charges). Use when the user says "paper
  trade", "practice trading", "trade with fake money", "virtual trading",
  "start the paper desk", "how is my paper account doing", "open the
  dashboard", "morning plan", "review today's trades", or wants to test the
  strategy before risking real money.
argument-hint: "[init | run | status | trades | stats | avoid | close | note]"
---

# trader-paper

The Paper Trading Desk is the pack's broker for fake money. The market is
real: live NSE prices, the real opening range, real news, Zerodha's real
charges. Only the money is fake. It exists so the user can find out, with
30 or more trades, whether this strategy makes money after charges before a
single real rupee is at risk.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/paper.py" init --capital 10000       # ₹10,000 of fake money + the rules
python3 "${CLAUDE_SKILL_DIR}/../trader-scanner/scan.py" universe --index nifty100   # what it scans
sh "${CLAUDE_SKILL_DIR}/start.sh"                                  # dashboard + engine for the day
python3 "${CLAUDE_SKILL_DIR}/paper.py" status                     # equity, cash, open positions
python3 "${CLAUDE_SKILL_DIR}/paper.py" trades --date 2026-10-26   # the journal
python3 "${CLAUDE_SKILL_DIR}/paper.py" stats                      # win rate, avg R, expectancy, drawdown
python3 "${CLAUDE_SKILL_DIR}/paper.py" avoid HDFCBANK --reason "RBI policy today"
python3 "${CLAUDE_SKILL_DIR}/paper.py" close SBIN                 # exit at market on the next tick
python3 "${CLAUDE_SKILL_DIR}/paper.py" note "Morning plan: ..."   # shows on the dashboard
python3 "${CLAUDE_SKILL_DIR}/dashboard.py"                        # http://127.0.0.1:8787 on its own
```

Run everything from the user's trading folder (the one holding `trader/`).
On macOS with python.org's Python, HTTPS needs certificates: `start.sh` sets
`SSL_CERT_FILE=/etc/ssl/cert.pem` when it is missing.

## Who does what

**The engine (`paper.py run`) does the mechanical work, by itself, every 30
seconds.** It needs no chat and no yes: the money is fake and the user chose
auto-trading. The rules are the pack's own, in this order, for every fresh
breakout (the 5-minute bar that just closed is the first close above the
9:15 to 9:30 high):

1. **Plan** (trader-exit): stop at the opening-range midpoint, rounded down to
   the NSE tick; target `target_r` (2) times the risk.
2. **Replay** (trader-timemachine): 2,000 replays from the stock's own 60 days
   to the flat-by time. A verdict that leans or strongly leans toward the stop
   is skipped (`tm_gate`, `tm_skip`).
3. **Size** (trader-budget): the most shares whose loss at the stop plus
   charges fits `max_loss_per_trade_inr`, within the position cap and the cash.
   A stock where not even one share fits is skipped.
4. **Risk** (trader-risk): the same PASS / BLOCK as a real order: the window
   (9:30 to 14:45), the daily loss limit, trades per day, reward:risk, cash.
5. **Fill**: a PASS is bought at the live price plus 0.05% slippage, rounded up
   to the tick. On every poll the stop is checked first (a fall through it
   fills at the stop, a gap below it at the bar's open), then the target.
   15:10: anything still open is sold at the live price.

Every one of those is logged as a call (enter, skip, exit, avoid) with its
reason, and the dashboard shows them as they happen.

**Claude does the judgement work, around the engine:**

- **Morning plan (8:45 to 9:10 IST).** Run `/trader-news` on the universe's
  biggest names and the holdings, `/trader-smartmoney new`, and look for:
  results today (the engine avoids those on its own from NSE's feed), an RBI
  policy or Budget day, an F&O ban, a pledge, a block deal or OFS, a stock in
  the news for a reason a breakout cannot price. `paper.py avoid SYMBOL
  --reason "..."` for each, then `paper.py note` the plan in three lines: the
  market mood (NIFTY's pre-open move), what is avoided and why, and anything
  the user should watch. Check `paper.py status` that the engine is running.
- **During the day (only if the user asks or a check is scheduled).**
  `paper.py status`; explain an entry or a skip from the decision log in
  plain words. `paper.py close SYMBOL` only if the user asks.
- **Evening review (after 15:35).** `paper.py trades --date TODAY` and
  `paper.py stats`. Write a short review with `paper.py note`: each trade in
  one line (why it was taken, how it ended, the R), what the charges took,
  and what the running stats say. Be honest: under 30 trades nothing is
  proven, a good day is not an edge, and a coin flip is a coin flip.

Claude never changes the rules mid-day to rescue a trade, never moves a stop,
and never edits the state file by hand.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules; `init` writes them for the capital: 1% max loss a trade, 3% a day, 3 trades a day, flat by 15:10, plus the paper keys below |
| `trader/universe.txt` | the stocks it scans (`scan.py universe --index nifty100`) |
| `trader/paper/state.json` | the account, open positions, every trade, every call, the equity curve, the stats. The dashboard reads it |
| `trader/paper/inbox.jsonl` | `avoid`, `unavoid`, `close` and `note` commands, picked up on the engine's next tick |
| `trader/paper/engine.log`, `dashboard.log` | what `start.sh` writes |

Paper keys in the config: `target_r` (2.0), `slippage_pct` (0.05),
`poll_seconds` (30), `max_confirm` (30 charts confirmed per scan), `min_price`
(₹50), `tm_gate` (true), `tm_replays` (2000), `tm_skip`, `avoid_results_and_exdates` (true).

## Honest limits

- **Prices are Yahoo's.** Free and unofficial; NSE quotes there can lag the
  exchange by up to about a minute, and a stop can be passed between polls.
  The fill model is conservative (slippage on every fill, a stop and a target
  in one bar count as the stop, gaps fill at the open) but it is not a real
  order book: a thin stock fills worse in real life.
- **The engine must be running.** It runs on this Mac (`start.sh` keeps it
  awake). If it is not running at 15:10, the next start closes the position
  at its last known price with Zerodha's ₹50 + GST auto square-off charge.
- **Small capital is part of the lesson.** With ₹10,000 many NIFTY stocks
  cannot be bought at all, one trade can use most of the cash, and charges
  take a real share of every win. The dashboard shows exactly how much.
- **Paper results flatter.** No nerves, no hesitation, no fat fingers. Before
  going live, the stats should be positive after charges over 30+ trades,
  and even then start small.

## Example: a replay of 16 to 23 October 2026 (NIFTY 50, ₹10,000)

```
09:35  INFY        ENTER     bought 6 at 1150.50 (signal 1149.85), stop 1134.90, target 1179.80, max loss ₹96.97 with charges
09:35  TITAN       SKIP      size: one share of TITAN costs ₹4,460.10, more than your cash limit ...
15:10  INFY        EXIT      flat by 15:10: sold 6 at 1164.40, P&L ₹76 after ₹7.40 charges (+0.81R)
...
11:50  SBIN        EXIT      stop: sold 9 at 968.80, P&L -₹105.16 after ₹9.31 charges (-1.15R)
...
12:55  BAJFINANCE  EXIT      target: sold 9 at 937.60, P&L ₹135.99 after ₹8.91 charges (+1.67R)
Paper account (replay): equity ₹10,067.57, total P&L ₹67.57 (+0.68%), charges paid ₹38.98, 7 trades.
```

Seven trades, three won: +₹67.57 after ₹38.98 of charges, which took 37% of
the gross. On 23 October the engine avoided TECHM (ex-date that day). Seven
trades prove nothing either way: that is what the next 30 are for.

## What it never does

It never sends an order to a broker, never touches real money, never trades
F&O, margin or shorts, and never claims a paper result predicts a real one.

## Safety

- Education and tooling, not financial advice; this pack is not a SEBI-registered adviser.
- Paper only: this skill cannot place a real order. Real orders go through trader-broker, one
  explicit yes at a time, and only after the user switches `"paper"` off themselves.
- Most individual intraday traders lose money (SEBI's study: 7 in 10 in the cash market).
