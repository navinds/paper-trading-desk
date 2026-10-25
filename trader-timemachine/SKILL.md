---
name: trader-timemachine
description: >-
  Time Machine for NSE intraday trades. Replays every possible outcome of a
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit (entry, stop,
  target), rebuilds the rest of the day 10,000 times from the stock's own
  5-minute bars over the last 60 days (9:15 to 15:15 IST), and re-runs the
  same rule on every past session. Prints the odds of target first, stop
  first, or neither by your flat-by time, where the price sits then, the
  average R, the history, and one honest verdict, then hands off to
  /trader-budget. Use when the user says "run the time machine", "replay
  this trade", "what are the odds", "simulate this trade", "monte carlo",
  "backtest this setup", "how often does this hit the target", "is this a
  coin flip", or after /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session. It is a simulation built from the stock's own
recent bars, not a prediction.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:10      # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:10 --write   # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-19 \
    --entry 1227.80 --stop 1215.30 --target 1240.30 --entry-time "9:50 AM" --until 15:10
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-10-19 09:50"` (IST) to pin the clock.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range, flat_by |
| Yahoo chart API | one request: 60 days of 5-minute bars of SYMBOL.NS (`range=60d&interval=5m`), free and unofficial |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: the 72
   five-minute bars from 9:15 to 15:15 IST. The NSE trades to 15:30, but
   Yahoo leaves the 15:15 to 15:30 bars of NSE stocks empty on most days,
   and an MIS trade is out before 15:25 anyway. Sessions with a missing bar
   before 15:15, and today, are left out. Every bar becomes four ratios to
   the bar before it: open, high, low and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default 15:15; pass
   `--until 15:10`, the default flat-by). Green, which means target first or
   above the entry at `--until`. R is +1 at a 1R target, -1 at the stop, and
   otherwise the distance from the entry at `--until` divided by the risk.
   A gap through the stop still counts as -1R here. A real fill can be worse,
   and charges come on top (trader-budget counts them).
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R, rounded to the NSE tick) runs again on every one of those
   past sessions. That is what really happened, not a simulation.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither, and the average R when its sign goes against the lean.

**The seed.** Same plan, same data, same seed: the same numbers, on any
machine. Say the seed with every result so the user can repeat it.

It refuses, in one plain line, when there are fewer than 20 full sessions,
when the entry bar is not in the data, or when the plan does not make sense.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`.
2. Run `timemachine.py run --until 15:10 --write` (or the plan's flat-by).
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at `--until`, the average R, the history line, then
   the verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. If
   the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news, a circuit or an F&O ban can do things those 60 days never did.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: RELIANCE, Monday 19 October 2026

Entry ₹1,227.80 at 9:50 AM (bar 6), stop ₹1,215.30 (the range midpoint,
rounded down to the tick), target ₹1,240.30 (1R, risk ₹12.50 a share):

```
10,000 replays of the rest of the session, 9:50 AM to 3:10 PM (64 five-minute bars), seed 19.
Each bar is drawn whole from RELIANCE's own 5-min bars at the same time of day (+-2 bars) over its last 55 full sessions (2026-07-29 to 2026-10-16).
  target first   16.6 %
  stop first     17.1 %
  neither        66.3 %   still open at 3:10 PM
  green          49.0 %   target first, or above the entry at 3:10 PM
  Price at 3:10 PM: median 1227.53, 90 % of replays between 1213.29 and 1243.07.
  Average R: -0.014
History: the same rule (15-min opening range, stop at the opening-range midpoint, 1R target) on each of those 55 real sessions fired 26 times: 13 target, 10 stop, 3 neither. Average R +0.058.
Close to a coin flip: target first 16.6 % vs stop first 17.1 %.
66.3 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those.
```

A coin flip, and two thirds of the replays end at the flat-by with neither
level touched: RELIANCE's 60 days were quiet relative to a ₹12.50 stop. The
real history was slightly better than even, before charges. That is what the
user should hear before `/trader-budget` sizes 36 shares.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Always say the seed.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
