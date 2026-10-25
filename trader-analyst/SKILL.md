---
name: trader-analyst
description: >-
  Earnings Analyst for Indian companies. Reads a company's quarterly results
  and sums up what changed: revenue, gross, operating and net margins, net
  profit and diluted EPS, quarter by quarter on the Indian fiscal year (Q1 =
  April to June), each against the same quarter a year earlier, with the
  last four quarters, in Rs crore. Finds the company's results filing on NSE
  and the board meeting that will consider the next results (the results
  date), and flags when the free data lags a filing (SEBI's 45-day rule).
  Reads a concall transcript or an investor presentation when you paste one.
  Use when the user says "how were Reliance's results", "analyse the
  quarterly results", "Q2 numbers", "what changed this quarter", "is revenue
  growing", "compare margins", "sum up the concall", "when are the results",
  or before trading a stock around its results.
argument-hint: "[symbols]"
---

# trader-analyst

Results move Indian stocks more than anything else on the calendar, and the
press release puts the best number first ("EBITDA up 20%" in a quarter where
net profit fell). This skill lines up the reported numbers quarter by
quarter and says plainly what changed. For an intraday trader it answers two
questions before a trade: what did the last results say, and are the next
results inside this trade?

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS INFY --metrics trader/metrics.json
python3 "${CLAUDE_SKILL_DIR}/facts.py" HDFCBANK --quarters 8 --json
```

## What it reads and writes

- The numbers: Yahoo Finance's fundamentals timeseries for SYMBOL.NS:
  quarterly total revenue, gross profit, operating income, net income and
  diluted EPS (consolidated, as Yahoo maps them). Free and unofficial: it can
  lag a filing and can miss a quarter, and the tool says when it does.
- NSE's public RSS feeds `Financial_Results.xml` (results just filed, with
  the XBRL link) and `Board_Meetings.xml` (meetings ahead to consider
  results: the next results date). They hold the last few days only.
- Company names from `trader/universe.txt` (scan.py universe), to find the
  company in NSE's feeds.
- Writes `trader/metrics.json` with `--metrics`: one row per company (last
  four quarters' revenue, profit and EPS, the latest margins and growth).
  `metrics.example.json` here is a real run for RELIANCE, TCS and HDFCBANK.
- Concalls: the tool does not fetch transcripts. NSE posts them as filings
  ("Analysts/Institutional Investor Meet/Con. Call Updates", /trader-news
  shows them). If the user pastes one, read it and sum up what management
  said changed: guidance, margins, demand, capex, anything they declined to answer.

## The rules

- **Filings win.** The table is Yahoo's mapping of the filed numbers. When
  the tool prints a NOTE that a newer quarter should be out (SEBI's listing
  rules: results within 45 days of the quarter, 60 for the March quarter) or
  that NSE's results filing is newer than Yahoo's last quarter, read the
  filing itself (the PDF or XBRL on nseindia.com) before summarising.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. Quarter-on-quarter is shown but many businesses are seasonal
  (retail and autos around festivals, IT in Q1 with wage hikes).
- **Say what is missing.** A quarter Yahoo does not have is shown as
  missing, never guessed. The last-four-quarters line is not summed then.
- **Flag one-offs.** When net profit is above operating profit, other income
  or an exceptional gain is inside it. Banks, NBFCs and insurers have no
  gross or operating margin here: blank, not zero.
- **Standalone and consolidated.** Indian companies file both. Yahoo's
  numbers are consolidated; say so, and say which one any filing you quote is.

## Steps

1. Run it for the companies in question (or the symbols on `trader/watchlist.json`).
2. Open the latest results filing (the NSE link, or the company's investor
   page) when the numbers need context or the tool flags a lag, and read it.
3. Write the summary: one line on the quarter (revenue, profit, EPS, growth),
   one on margins, one on anything unusual (a one-off, a missing quarter,
   data that lags), and the links. Plain words, real numbers, no adjectives
   the numbers do not support.
4. If the next results fall inside the planned trade (a carry overnight, or
   results announced during market hours, which Indian companies do), say it
   before `/trader-exit` sets the stop: a stop does not protect against a gap.

## Example: run on 25 October 2026

`facts.py RELIANCE` (offline on the 2026-10-25 data): Q1 FY27 (quarter to
2026-06-30) revenue Rs 3,09,468 crore, +27.0% on a year earlier; net profit
Rs 20,946 crore, -22.4%; diluted EPS Rs 15.48 (-22.4%); operating margin 10.5%
(+0.5 points on Q4 FY26). Yahoo has no Q2 FY26 (September 2025) quarter, so
the last four quarters are not summed: the summary says to check that
quarter's filing on NSE. Q2 FY27 results were reported mid-October and are
not in Yahoo's data yet, so the summary reads them from the NSE filing.

`facts.py HDFCBANK`: Yahoo's numbers stop at Q1 FY26 (June 2025), and the tool
prints the NOTE that Q1 FY27 results were due by now. No margins: a bank.
`facts.py TCS`: four full quarters to Q2 FY27, last-four-quarters revenue Rs
2,83,248 crore and diluted EPS Rs 142.66.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell, and never gives a
  target price. It reports what the company filed.
- Never invents a number the data does not have, or estimates a missing quarter.

## Safety

Education and tooling, not financial advice; this pack is not a SEBI-registered
research analyst. This skill only reads filings and data. Nothing places an
order except `/trader-broker`, after `/trader-risk` passes and the user types
an explicit yes for that order.
