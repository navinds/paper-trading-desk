---
name: trader-scanner
description: >-
  Chart Scanner for the Indian market (NSE). Scans thousands of NSE charts
  for setups: opening-range breakouts, bull flags, 20-day breakouts,
  oversold bounces. Builds the list of NSE equities from NSE's own equity
  list (series EQ, the stocks you can trade intraday; BE and BZ
  trade-to-trade stocks left out) or a NIFTY index (NIFTY 50, 100, 200, 500,
  Midcap 150, Smallcap 250, Bank), runs a pre-market scan of daily closes
  and an intraday opening-range breakout scan on 5-minute bars from 9:15
  IST, then writes trader/watchlist.json for trader-news and trader-entry.
  Free Yahoo data (SYMBOL.NS), bar by bar. Use when the user says "scan the
  market", "scan Nifty 50", "find me setups", "what is breaking out", "ORB
  scan", "opening range breakouts", "bull flags", "oversold stocks",
  "pre-market scan", "build my watchlist", "what should I trade today", or
  at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of an intraday trade. It reads thousands
of free charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and
a setup on a chart is not a prediction. SEBI's own study found 7 in 10
individual intraday traders in the cash market lost money; the scanner does
not change that. It only saves you from flipping through 2,000 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # NSE's EQ list -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty500      # or one NIFTY index
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before 9:15 -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:00                 # cut the clock at 10:00 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 5,00,000 shares,
orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:00"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run.

Symbols are NSE symbols as Zerodha shows them: RELIANCE, M&M, BAJAJ-AUTO.
The tool asks Yahoo for RELIANCE.NS itself.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Company name` a line; read by `daily`, `orb`, trader-news and trader-analyst (company names) |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:45) |

## The universe

`universe` downloads `EQUITY_L.csv` from nsearchives.nseindia.com, NSE's
list of every listed equity, and keeps series EQ. Series BE and BZ are
trade-to-trade: every trade there settles by delivery, so there is no
intraday in them. On the 2026-10-25 file that is 2,359 EQ stocks out of
2,613 rows (221 BE and 33 BZ left out). `--index nifty500` (or nifty50,
niftynext50, nifty100, nifty200, niftymidcap150, niftysmallcap250,
niftybank) keeps an index's constituents instead, from NSE's published
lists. For intraday, the NIFTY 200 or 500 is a sensible start: liquid,
tight spreads, and in the F&O list mostly, so circuits are wide.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,359
symbols take 118 requests (about a minute with the polite pause). Liquidity
comes from the spark meta: last price at least `--min-price` and the last
session's volume at least `--min-volume`. Run it before 9:15 or after 15:30:
during the session the volume is the day's so far, and today's daily bar is
left out until the close.

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars (the pre-open auction runs 9:00 to 9:08 and
sets the 9:15 open). A hit is the first completed 5-minute bar to CLOSE
above the range high, at that bar's close time. The first possible hit is
the 9:30 bar, closing 9:35.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range (necessary, so no breakout is lost).
2. Stage 2, each candidate: the v8 chart with real highs and lows. On a
   strong morning most charts are candidates, so stage 2 checks at most
   `--max-confirm` (200) of them, the most traded first, and prints PARTIAL
   with the count it skipped. `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt, a circuit lock or a thin stock. After
`no_entries_after` (14:45) a breakout is expired and the scan says so.

## Example: Monday 19 October 2026, NIFTY 50, scan at 10:00

```
python3 scan.py orb --offline wall5m-nifty50.json.gz --now "2026-10-19 10:00"
Fetched 50 of 50 charts; 50 usable; 22 candidates, 22 confirmed with real highs and lows.
8 of 50 charts closed above their opening-range high by 10:00 AM:
   9:35 AM  SBIN            ₹978.95  range 960.90 to 977.70  +0.13% over
   9:35 AM  ULTRACEMCO   ₹11,056.00  range 10,941.00 to 11,043.00  +0.12% over
   9:40 AM  HCLTECH       ₹1,396.50  range 1,359.40 to 1,388.00  +0.61% over
   ...
   9:50 AM  RELIANCE      ₹1,227.80  range 1,207.30 to 1,223.40  +0.36% over
```

The user picks RELIANCE and Claude hands it to `/trader-news RELIANCE`, then
`/trader-entry RELIANCE`. Before the open that morning (closes through Fri
16 Oct) the daily scan tagged INFY, BHARTIARTL, TCS, ICICIBANK and AXISBANK a
20-day breakout and M&M and BAJFINANCE a reclaim of the 20-day average.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, never tick by tick. Partial results are said as partial.
- NSE trading holidays for 2026 (NSE's holiday list, read 2026-10-25). NSE
  publishes next year's list in December; until then the tool says the date
  is outside its calendar. The Diwali Muhurat session is not covered.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner", "a multibagger" or "a sure shot".
- Never print a P&L, a return or a profit figure, and never give "tips".
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation. This pack is not a SEBI-registered adviser or research analyst.
- Paper trading by default (`"paper": true`). Zerodha has no paper account, so paper means simulated fills; going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools need approval. Never allow an
  order tool without asking.
- No F&O, margin (MTF or MIS leverage) or short selling unless the config allows it and the user confirms.
- Kite logs in through Zerodha's own login page. Never ask for, read or repeat a password, PIN, TOTP or API secret.
