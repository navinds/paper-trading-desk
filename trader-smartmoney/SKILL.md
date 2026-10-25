---
name: trader-smartmoney
description: >-
  The Smart Money Tracker, India's answer to the Pelosi tracker. Watches the
  trades big investors and company insiders must make public: NSE's daily
  bulk deals (one client trading over 0.5% of a company's shares in a day)
  and block deals (single trades of Rs 10 crore or more), and SEBI insider
  trading (PIT) disclosures by promoters, directors and key staff: who,
  their category, buy, sell or pledge, how many shares, the value, the mode
  (market, off-market, ESOP) and the holding before and after. Flags deals
  in stocks you hold or watch, names on your watch list (Damani, Kacholia,
  Kedia, Dolly Khanna, LIC, foreign funds...), promoter market buys and
  pledges, and marks prop and HFT firms whose deals are churn. Use when the
  user says "what is Kacholia buying", "track big investors", "bulk deals
  today", "block deals", "any promoter buying", "insider trading
  disclosures", "superstar portfolio", "smart money", "FII deals", "is
  anyone big buying this stock", or "run the smart money skill".
argument-hint: "[deals | insider | new] [--client NAME] [--symbol SYMBOL]"
---

# trader-smartmoney

India has no Congress-style stock disclosures for its MPs, but it has
something more useful for a trader: every big trade in a single stock and
every trade by a company's own insiders must be made public, fast.

- **Bulk deals**: a client trading more than 0.5% of a company's listed
  shares in a day. NSE publishes them after the close, by name.
- **Block deals**: single trades of Rs 10 crore or more in the block-deal
  windows. Also by name.
- **Insider trades**: under SEBI's Prohibition of Insider Trading rules,
  promoters, directors and designated people disclose trades above the
  threshold (Rs 10 lakh in a quarter) within 2 trading days; the company tells
  the exchange within 2 more. NSE posts each disclosure as an XBRL file.

This skill reads those public files the moment NSE posts them. It is fast.
It is not early: a bulk deal is after the close, a disclosure days after the
trade. There is no inside information here. Say that every time.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/deals.py" deals                         # the day's bulk and block deals, biggest first
python3 "${CLAUDE_SKILL_DIR}/deals.py" deals --client kacholia       # one investor
python3 "${CLAUDE_SKILL_DIR}/deals.py" deals --watched --hide-prop   # only the names in trader/smartmoney.json
python3 "${CLAUDE_SKILL_DIR}/deals.py" insider --promoters           # promoter and promoter-group trades
python3 "${CLAUDE_SKILL_DIR}/deals.py" insider --symbol BAJAJFINSV
python3 "${CLAUDE_SKILL_DIR}/deals.py" new --read                    # only what is new since the last check
```

## What it reads and writes

- Reads `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv`
  (the latest trading day), `content/RSS/Insider_Trading.xml` and each
  disclosure's XBRL file (`--max-read 25` per run, half a second apart).
- Writes `trader/state/smartmoney.json` (what was already shown, so `new`
  alerts once) and keeps a copy of each day's deal files in
  `trader/state/deals/` (NSE's own files hold one day, so a history builds up).
- Reads `trader/smartmoney.json` (copy `smartmoney.example.json`): the
  investor names to flag, matched by part of the name, any case.
- Reads `trader/portfolio.json`, `trader/watchlist.json` and `trader/plan.json`
  to flag deals and trades in your stocks.

## The rules

- **Quote the filing.** Every row comes from NSE's file, with the link for
  insider trades. Third-party "superstar portfolio" sites are fine for
  browsing but lag and guess; the filing wins.
- **Prop is not a view.** Prop and HFT firms (Graviton, iRage, Jump, HRTI, QE
  Securities and the like) buy and sell the same stock in size every day to
  make markets. Their bulk deals are marked `prop`; `--hide-prop` drops them.
  A buy and a sell of the same size by one name the same day is churn.
- **Mode matters.** A promoter's market purchase is a real view with their own
  money; an ESOP allotment, an inter-se transfer between promoter companies,
  an off-market gift or a pledge is not a buy. The tool flags `PROMOTER MARKET
  BUY` only for a market purchase, and `pledge` on pledges, which are a risk
  sign (an invoked pledge forces selling).
- **Say the delay.** Bulk and block deals: after the close of the day shown.
  Insider trades: the trade date and the date the company was told are both
  printed. When NSE's insider feed itself lags, the tool says so.

## Steps

1. `deals.py deals --hide-prop` after 17:00 IST for the day's deals, or
   `deals.py new --read` on a schedule (once an evening is plenty: NSE posts
   the deal files once a day).
2. For each flagged row, say who, what, how much, at what price, and the flag.
   Check `/trader-news` for the stock: a block deal often comes with an OFS,
   a stake sale or a fund raise that explains it.
3. If the user wants to act on it, it goes the long way: a deal is a reason to
   look at a chart, not a setup. `/trader-scanner` and `/trader-entry` decide
   if there is a trade, sized by `/trader-budget`, checked by `/trader-risk`,
   placed by `/trader-broker` only after their yes.

## Example: Friday 23 October 2026

`deals.py deals --hide-prop` on that day's file (68 deals without prop firms):

```
NSE bulk and block deals, 2026-10-23: 68 deals, biggest first.
  2026-10-23 bulk   TONBO        SELL    24,00,000 @ 1069.22   Rs 256.6 cr    YALI DEEPTECH FUND I
  2026-10-23 bulk   SMT          BUY     28,07,366 @ 371.44    Rs 104.3 cr    INVESCO MUTUAL FUND
  2026-10-23 bulk   TONBO        BUY      9,00,000 @ 1134.96   Rs 102.1 cr    BAJAJ LIFE INSURANCE LIMITED
```

Without `--hide-prop` the top of the list is Graviton buying and selling
about 1.4 crore SWIGGY shares each way, marked prop: churn. In NSE's insider
feed, Bajaj Holdings & Investment (promoter) bought 20,90,050 BAJAJFINSV
shares in the market on 27 April for about Rs 370 crore (holding 38.28% to
38.41%, flagged PROMOTER MARKET BUY), while Bajaj Auto Holdings (promoter
group) sold the same number: shares moving inside the promoter group, not
new money. Then: "These are public filings, after the fact. They say what
was traded, not what anyone will do next."

## What it never does

- Never says or implies anyone has inside information, or that copying an
  investor beats the market. No return claims.
- Never presents a bulk deal as a long-term holding: the same name may sell
  the next day.
- Never places an order, and never copies a trade automatically.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything; it is not a SEBI-registered adviser.
- Nothing places an order except `/trader-broker`, and only after
  `/trader-risk` passes AND the user types an explicit yes for that order in
  the chat, AND the Kite order tool needs approval so Claude's own
  "Allow once / Deny" card appears too.
- Bulk, block and insider-trading data are PUBLIC disclosures. This skill
  reads them fast; it has no inside information, and it says so.
