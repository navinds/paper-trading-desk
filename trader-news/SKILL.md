---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch
  and flags what moves it: Google News India headlines per company, the
  company's own filings with NSE (financial results, board meeting outcomes,
  director and auditor changes, credit ratings, pledges, fund raising,
  acquisitions), board meetings ahead that will consider results (the next
  results date), ex-dates for dividends, bonus issues and splits, and how
  far each stock is from yesterday's close. Shows only what is new since the
  last check, newest first, in IST, tags each item (earnings, analyst,
  legal, deal, capital, people, risk, halt, regulatory, macro) and never
  repeats one. Use when the user says "any news on Reliance", "why is this
  stock moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "check NSE announcements", "when are the results",
  "did anything break", or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results day, a board
outcome, a pledge invocation or an ex-dividend date. Before a setup becomes a
trade, this skill reads the feeds that carry those first, for every stock on
the watchlist, and tells you only what is new. It also prints how far each
stock has moved today, so a headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | company names (scan.py universe): the headline search and the NSE match use them. Without it, NSE filings match only by file name, so run `scan.py universe` once |
| Google News, India edition | `news.google.com/rss/search?q="Reliance Industries" OR "RELIANCE share"&hl=en-IN&gl=IN` |
| NSE RSS: `Online_announcements.xml` | every corporate announcement, market-wide, matched to your companies |
| NSE RSS: `Board_Meetings.xml` | board meetings ahead and their purpose: "Financial Results" is the results date |
| NSE RSS: `Corporate_action.xml` | ex-dates: dividend, bonus, split. On the ex-date the price drops by the dividend; that is not a breakdown |
| Yahoo Finance chart endpoint | the last price against yesterday's close (SYMBOL.NS; unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

The NSE feeds are NSE's own public RSS files on nsearchives.nseindia.com,
one request each for the whole market. They hold the last few days.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE filing and a headline disagree, the
  filing is what the company told the exchange.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags. Items marked `!` are filings a day trader must read: financial
  results, board outcomes and board meetings, resignations, changes of
  director, KMP or auditor, credit ratings, pledges, acquisitions and
  schemes, fund raising (QIP, preferential, rights), buybacks, defaults,
  insolvency, fraud, suspension, open offers, OFS, and every ex-date.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too: check for a block
  deal (`/trader-smartmoney`) or a circuit.
- **No predictions, no tips.** Report what was published and how far the
  price is from yesterday's close. "Stocks to buy" lists from brokers and TV
  are tagged analyst: they are opinions, say whose.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: every `!` filing, anything tagged earnings,
   halt, legal, regulatory or risk, and every MOVING symbol first.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   on 28 October after market hours: an MIS trade is out before, a carry is
   exposed to the gap").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: a results date, an ex-date, a pledge, an F&O ban. Never
   act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning at 8:45 IST.

## Example: Sunday 25 October 2026, 21:00 IST

`news.py --tickers RELIANCE GMRAIRPORT IIFL LTTS` on the real feeds:

```
RELIANCE   ₹1,190.00 +0.46% vs yesterday's close ₹1,184.50 at 15:28 IST
! 2026-10-25 20:11 IST  IIFL        NSE filing       IIFL Finance Limited: Change in Director(s)
! 2026-10-25 19:39 IST  GMRAIRPORT  NSE filing       GMR AIRPORTS LIMITED board meeting: Financial Results;Meeting Date: 28-Oct-2026
  2026-10-25 17:24 IST  LTTS        NSE filing       L&T Technology Services Limited: Analysts/Institutional Investor Meet/Con. Call Updates
  2026-10-24 22:21 IST  RELIANCE    TradingView      Reliance Industries Says Jio Platforms IPO Issue Price Set At Upper End Of Price Band ...
! 2026-10-23 06:10 IST  LTTS        NSE filing       L&T Technology Services Limited - Ex-Date: 27-Oct-2026 corporate action: INTERIM DIVIDEND - RS 20 PER SHARE
```

The brief: "GMR Airports reports on 28 October: a day trade that day is a
results trade. LTTS goes ex-dividend Rs 20 on 27 October: the open will be
about Rs 20 lower for that reason alone. RELIANCE: the Jio Platforms IPO
priced at the top of its band." When a symbol has no news, say so rather
than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one search per symbol, one request per NSE feed,
  a pause between calls.

## Safety

Education and tooling, not financial advice; this pack is not a SEBI-registered
adviser. This skill only reads and reports. Nothing places an order except
`/trader-broker`, after `/trader-risk` passes and the user types an explicit
yes for that order.
