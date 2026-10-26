# Paper Trading Desk

Paper trading on the live NSE market with fake money, run for free on GitHub Actions.

- **Dashboard:** https://navinds.github.io/paper-trading-desk/ (updates every few minutes)
- **Account:** ₹10,000 of fake money. Max loss ₹100 a trade, ₹300 a day, up to 3 trades a day, flat by 15:10 IST.
- **Strategy:** 15-minute opening-range breakouts on the NIFTY 100. Stop at the range midpoint, target 2R. Every
  trade is replayed in the time machine, sized with Zerodha's charges, and risk-checked before the simulated fill.

## How it runs

GitHub Actions (`.github/workflows/desk.yml`) tries to start the desk every 30 minutes from 07:30 to 15:30 IST
on weekdays, because GitHub's schedule is best effort (some starts are late, some never happen). One run trades
at a time; the others wait. Each run stops before GitHub's 6-hour limit or at 15:35 IST, and the engine
(`trader-paper/paper.py`) saves the account to the `data` branch every 5 minutes, so the next run carries on
from there. NSE holidays: the engine exits at once.

Before trusting it, run **Actions > data check > Run workflow** once: it checks that Yahoo Finance and NSE
answer GitHub's servers.

## Honest limits

- Scheduled GitHub jobs often start late, sometimes by 30 minutes or more. A late morning run misses the
  first breakouts; a late afternoon run leaves a position unwatched for a while (the engine then checks the
  bars it missed, so a stop hit in the gap still counts at the stop price).
- Prices are Yahoo's: free, unofficial, can lag the exchange by up to about a minute.
- Paper results flatter real ones. 30+ trades before reading anything into the stats.
- This repo is public: the trades are visible to anyone. No broker, no keys, no real money is involved.

Not financial advice. Built from the India edition of
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT).
