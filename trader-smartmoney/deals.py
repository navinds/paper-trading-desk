#!/usr/bin/env python3
"""Smart Money Tracker for trader-smartmoney. Standard library only, no keys.

Reads the trades that big investors and company insiders must make public in
India, the moment NSE posts them:

  bulk and block deals   NSE's daily files. A bulk deal is one client
                         trading more than 0.5% of a company's shares in a
                         day; a block deal is a single trade of Rs 10 crore
                         or more in the block window. Name, buy or sell,
                         quantity and price.
  insider trades         SEBI's Prohibition of Insider Trading rules make
                         promoters, directors and key staff disclose their
                         trades (within 2 trading days of telling the
                         company). NSE posts each one as an XBRL file:
                         who, their category, buy / sell / pledge, how many
                         shares, the value, the mode (market, off-market,
                         ESOP) and the holding before and after.

    python3 deals.py deals                          # today's bulk and block deals
    python3 deals.py deals --client kacholia        # one investor, by name
    python3 deals.py deals --watched                # the investors in trader/smartmoney.json
    python3 deals.py insider                        # the latest insider disclosures
    python3 deals.py insider --symbol LALPATHLAB --promoters
    python3 deals.py new --read                     # only what is new since the last check

Sources (public files NSE publishes for exactly this, no login):
  https://nsearchives.nseindia.com/content/equities/bulk.csv
  https://nsearchives.nseindia.com/content/equities/block.csv
  https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml, and each
  disclosure's XBRL file it links to (nsearchives.nseindia.com/corporate/xbrl/)
The deal files hold the latest trading day only: `deals` keeps a copy of each
day it sees in trader/state/deals/ so a history builds up as you run it.

Flags: a deal or a trade in a stock you hold or watch (trader/portfolio.json,
trader/watchlist.json, trader/plan.json); a name on your watch list
(trader/smartmoney.json, see smartmoney.example.json); a promoter buying in
the market. Prop and high-frequency firms (IRAGE, GRAVITON, QE SECURITIES and
the like) fill the bulk-deal file every day: their deals are marked "prop",
they are churn, not a view on the company.

All of this is public and after the fact. It says what someone did, not why,
and not what anyone will do next. State: trader/state/smartmoney.json.
Offline: --offline DIR reads bulk.csv, block.csv, Insider_Trading.xml and the
XBRL files (by their file names) from DIR.
"""

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://nsearchives.nseindia.com/content/"
BULK_URL = BASE + "equities/bulk.csv"
BLOCK_URL = BASE + "equities/block.csv"
INSIDER_RSS = BASE + "RSS/Insider_Trading.xml"
UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"
PROP = re.compile(r"\b(?:IRAGE|GRAVITON|QE SECURITIES|ALPHAGREP|TOWER RESEARCH|JUNOMONETA|NK SECURITIES|"
                  r"HRTI|SILVERLEAF|MUSIGMA|PACE STOCK|DOLAT|JUMP TRADING|CITADEL|XTX|OPTIVER|"
                  r"BEELINE BROKING|MATHISYS|ELIXIR|ACE STOCK)\b", re.I)
KEEP_DAYS = 60


class DealsError(Exception):
    pass


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url):
        name = url.rsplit("/", 1)[-1]
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise DealsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise DealsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise DealsError("could not reach %s: %s" % (url.split("/")[2], exc.reason))


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def my_symbols(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json", plan="trader/plan.json"):
    out = set()
    for p in (load_json(portfolio) or {}).get("positions", []):
        if p.get("ticker") or p.get("symbol"):
            out.add(str(p.get("ticker") or p.get("symbol")).upper())
    for h in (load_json(watchlist) or {}).get("hits", []):
        if h.get("symbol"):
            out.add(str(h["symbol"]).upper())
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.add(str(pl["symbol"]).upper())
    return out


def watched_names(path):
    doc = load_json(path) or {}
    return [str(n).strip() for n in doc.get("investors", []) if str(n).strip()]


# ---------------------------------------------------------------- deals

def parse_deals(text, kind):
    """NSE's bulk.csv / block.csv -> rows. "NO RECORDS" means no deal that day."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise DealsError("%s file is empty" % kind)
    hdr = [h.strip().lower() for h in rows[0]]
    if "symbol" not in hdr or "client name" not in hdr:
        raise DealsError("not NSE's %s deals file (header %s)" % (kind, ",".join(hdr[:4])))
    ix = {k: hdr.index(k) for k in hdr}
    price_col = next((k for k in hdr if k.startswith("trade price")), None)
    out = []
    for r in rows[1:]:
        if not r or r[0].strip().upper() == "NO RECORDS" or len(r) < len(hdr) - 1:
            continue

        def col(k):
            return r[ix[k]].strip() if k in ix and ix[k] < len(r) else ""
        try:
            qty = int(float(col("quantity traded").replace(",", "")))
            price = float(col(price_col).replace(",", "")) if price_col else None
        except ValueError:
            continue
        try:
            day = _dt.datetime.strptime(col("date"), "%d-%b-%Y").date().isoformat()
        except ValueError:
            day = col("date")
        client = " ".join(col("client name").split())
        out.append({"kind": kind, "date": day, "symbol": col("symbol").upper(), "company": col("security name"),
                    "client": client, "side": col("buy/sell").lower(), "qty": qty, "price": price,
                    "value": round(qty * price, 2) if price else None,
                    "prop": bool(PROP.search(client)), "remarks": col("remarks")})
    return out


def deal_id(d):
    return hashlib.sha1(("%(kind)s|%(date)s|%(symbol)s|%(client)s|%(side)s|%(qty)s|%(price)s" % d)
                        .encode("utf-8")).hexdigest()[:16]


def _today():
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=5, minutes=30)).date()


def flag_deal(d, mine, watched):
    flags = []
    if d["symbol"] in mine:
        flags.append("YOURS")
    for n in watched:
        if n.lower() in d["client"].lower():
            flags.append("WATCHED: " + n)
            break
    if d["prop"]:
        flags.append("prop")
    return flags


# ---------------------------------------------------------------- insider trades

def parse_insider_rss(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DealsError("the insider-trading feed is not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        def t(n):
            c = it.find(n)
            return " ".join((c.text or "").split()) if c is not None and c.text else ""
        out.append({"company": t("title"), "link": t("link"), "posted": t("pubDate"), "description": t("description")})
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_insider_xbrl(data):
    """One SEBI PIT disclosure (NSE's XBRL) -> rows, one per person and trade."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DealsError("the disclosure is not valid XML: %s" % exc)
    by_ctx = {}
    common = {}
    for el in root.iter():
        tag = _local(el.tag)
        if el.text is None or not el.text.strip():
            continue
        ctx = el.get("contextRef")
        if ctx is None:
            continue
        by_ctx.setdefault(ctx, {})[tag] = el.text.strip()
    for vals in by_ctx.values():
        for k in ("NSESymbol", "NameOfTheCompany", "ISIN"):
            if k in vals:
                common.setdefault(k, vals[k])
    rows = []
    for ctx, v in sorted(by_ctx.items()):
        if "NameOfThePerson" not in v:
            continue

        def num(k):
            try:
                return float(str(v.get(k, "")).replace(",", ""))
            except ValueError:
                return None
        qty = num("SecuritiesAcquiredOrDisposedNumberOfSecurity")
        val = num("SecuritiesAcquiredOrDisposedValueOfSecurity") or num("ValueInAggregate")
        rows.append({"symbol": (v.get("NSESymbol") or common.get("NSESymbol") or "").upper(),
                     "company": v.get("NameOfTheCompany") or common.get("NameOfTheCompany", ""),
                     "person": v.get("NameOfThePerson"), "category": v.get("CategoryOfPerson", ""),
                     "type": v.get("SecuritiesAcquiredOrDisposedTransactionType", ""),
                     "instrument": v.get("TypeOfInstrument", ""), "qty": int(qty) if qty is not None else None,
                     "value": val, "price": round(val / qty, 2) if val and qty else None,
                     "mode": v.get("ModeOfAcquisitionOrDisposal", ""),
                     "from": v.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate"),
                     "to": v.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate"),
                     "intimated": v.get("DateOfIntimationToCompany"),
                     "pct_before": num("SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding"),
                     "pct_after": num("SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding"),
                     "exchange": v.get("ExchangeOnWhichTheTradeWasExecuted", "")})
    return rows


def flag_insider(r, mine, watched):
    flags = []
    if r["symbol"] in mine:
        flags.append("YOURS")
    for n in watched:
        if n.lower() in (r["person"] or "").lower():
            flags.append("WATCHED: " + n)
            break
    if re.search(r"promoter", r["category"], re.I) and r["type"].lower() == "buy" and \
            re.search(r"market (?:purchase|buy)", r["mode"], re.I):
        flags.append("PROMOTER MARKET BUY")
    if r["type"].lower() == "buy" and re.search(r"sale", r["mode"], re.I):
        flags.append("check: a buy with mode %r" % r["mode"])
    if re.search(r"pledge", r["type"] + " " + r["mode"], re.I):
        flags.append("pledge")
    return flags


# ---------------------------------------------------------------- output

def _group(whole):
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + parts + [tail])


def inr(v):
    """Rs 12.3 cr / Rs 45.6 lakh / Rs 9,999: the way Indian filings speak."""
    if v is None:
        return "-"
    if v >= 1e7:
        return "Rs %.1f cr" % (v / 1e7)
    if v >= 1e5:
        return "Rs %.1f lakh" % (v / 1e5)
    return "Rs " + _group(str(int(round(v))))


def qty_words(n):
    return "-" if n is None else _group(str(int(n)))


def deal_line(d, flags):
    return "%s %-6s %-12s %-4s %12s @ %-9s %-14s %s%s" % (
        d["date"], d["kind"], d["symbol"][:12], d["side"].upper(), qty_words(d["qty"]),
        ("%.2f" % d["price"]) if d["price"] else "-", inr(d["value"]), d["client"],
        ("  [" + ", ".join(flags) + "]") if flags else "")


def insider_line(r, flags):
    pct = ""
    if r["pct_before"] is not None and r["pct_after"] is not None:
        pct = " (holding %.2f%% -> %.2f%%)" % (r["pct_before"], r["pct_after"])
    return "%s  %-12s %s, %s: %s %s shares%s, %s, mode %s, traded %s%s%s" % (
        r.get("intimated") or "-", r["symbol"][:12], r["person"], r["category"] or "category ?", r["type"].upper(),
        qty_words(r["qty"]), (" at about Rs %.2f" % r["price"]) if r["price"] else "", inr(r["value"]),
        r["mode"] or "?", r["from"] if r["from"] == r["to"] else "%s to %s" % (r["from"], r["to"]), pct,
        ("  [" + ", ".join(flags) + "]") if flags else "")


# ---------------------------------------------------------------- state

def load_state(path):
    return load_json(path) or {"seen": {}}


def save_state(path, state, today):
    cutoff = (_dt.date.fromisoformat(today) - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def archive(folder, kind, day, raw):
    """Keep a copy of each day's deals file: NSE's own file holds one day only."""
    if not folder or not day:
        return
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s-%s.csv" % (day, kind))
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(raw)


# ---------------------------------------------------------------- commands

def get_deals(fetcher, a, errors):
    out = []
    for kind, url in (("bulk", BULK_URL), ("block", BLOCK_URL)):
        try:
            raw = fetcher.get(url)
            rows = parse_deals(raw, kind)
            out += rows
            if rows and not a.no_archive:
                archive(a.archive, kind, rows[0]["date"], raw)
        except DealsError as exc:
            errors.append("%s deals: %s" % (kind, exc))
    return out


def get_insider(fetcher, a, errors, state=None, only_new=False):
    try:
        items = parse_insider_rss(fetcher.get(INSIDER_RSS))
    except DealsError as exc:
        errors.append("insider feed: %s" % exc)
        return [], []
    rows, posted, fetched, links = [], [], 0, set()
    newest = None
    for it in items:
        try:
            d = _dt.datetime.strptime(it["posted"][:11], "%d-%b-%Y").date()
            newest = max(newest, d) if newest else d
        except ValueError:
            pass
    today = _dt.date.fromisoformat(a.today) if a.today else _today()
    if newest and (today - newest).days > 14:
        errors.append("the newest item in NSE's insider-trading feed is from %s: the feed is lagging, so check "
                      "nseindia.com (Corporate Filings, Insider Trading) for recent disclosures" % newest.isoformat())
    for it in items:
        if it["link"] in links:
            continue                       # NSE's feed repeats some items
        links.add(it["link"])
        key = "pit:" + hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:16]
        if only_new and state is not None and key in state.get("seen", {}):
            continue
        posted.append((key, it))
        if fetched >= a.max_read:
            continue
        fetched += 1
        try:
            for r in parse_insider_xbrl(fetcher.get(it["link"])):
                r["posted"], r["link"], r["_key"] = it["posted"], it["link"], key
                rows.append(r)
        except DealsError as exc:
            errors.append("%s: %s" % (it["company"], exc))
    if len(posted) > fetched and fetched >= a.max_read:
        errors.append("read %d of %d disclosures (--max-read %d)" % (fetched, len(posted), a.max_read))
    return rows, posted


def filter_deals(deals, a):
    out = []
    for d in deals:
        if a.symbol and d["symbol"] != a.symbol.upper():
            continue
        if a.client and a.client.lower() not in d["client"].lower():
            continue
        if getattr(a, "hide_prop", False) and d["prop"]:
            continue
        out.append(d)
    return out


def cmd_deals(a, fetcher, mine, watched, errors):
    deals = filter_deals(get_deals(fetcher, a, errors), a)
    if a.watched:
        deals = [d for d in deals if flag_deal(d, set(), watched)] if watched else []
        if not watched:
            errors.append("no investors in %s (copy smartmoney.example.json there)" % a.watchlist_file)
    deals.sort(key=lambda d: (-(d["value"] or 0)))
    if a.json:
        print(json.dumps({"deals": [dict(d, flags=flag_deal(d, mine, watched)) for d in deals], "notes": errors},
                         indent=2, ensure_ascii=False))
        return 0 if deals else 1
    if deals:
        day = max(d["date"] for d in deals)
        print("NSE bulk and block deals, %s: %d deal%s, biggest first%s." % (
            day, len(deals), "" if len(deals) == 1 else "s",
            " (prop / HFT firms marked \"prop\": churn, not a view)" if any(d["prop"] for d in deals) else ""))
        for d in deals[:a.show]:
            print("  " + deal_line(d, flag_deal(d, mine, watched)))
        if len(deals) > a.show:
            print("  ... and %d more (--show N)." % (len(deals) - a.show))
    else:
        print("No bulk or block deals match.")
    for e in errors:
        print("note: " + e)
    print("Public, after the fact: what someone traded, not why, and not what comes next. Not financial advice.")
    return 0 if deals else 1


def cmd_insider(a, fetcher, mine, watched, errors):
    rows, _ = get_insider(fetcher, a, errors)
    if a.symbol:
        rows = [r for r in rows if r["symbol"] == a.symbol.upper()]
    if a.promoters:
        rows = [r for r in rows if re.search(r"promoter", r["category"], re.I)]
    if a.json:
        print(json.dumps({"trades": [dict({k: v for k, v in r.items() if k != "_key"},
                                          flags=flag_insider(r, mine, watched)) for r in rows],
                          "notes": errors}, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    if rows:
        print("Insider trades disclosed to NSE (SEBI PIT rules), newest first: %d." % len(rows))
        for r in rows[:a.show]:
            print("  " + insider_line(r, flag_insider(r, mine, watched)))
            print("      " + r["link"])
    else:
        print("No insider disclosures match.")
    for e in errors:
        print("note: " + e)
    print("A disclosure says what an insider did, days after it happened. It is public, not inside information. "
          "Not financial advice.")
    return 0 if rows else 1


def cmd_new(a, fetcher, mine, watched, errors, state, today):
    new_deals = []
    for d in filter_deals(get_deals(fetcher, a, errors), a):
        k = "deal:" + deal_id(d)
        if k in state["seen"]:
            continue
        state["seen"][k] = today
        flags = flag_deal(d, mine, watched)
        if flags and flags != ["prop"]:
            new_deals.append((d, flags))
    rows, posted = get_insider(fetcher, a, errors, state, only_new=True) if a.read else ([], [])
    for key, _ in posted:
        state["seen"][key] = today
    if a.json:
        print(json.dumps({"deals": [dict(d, flags=f) for d, f in new_deals],
                          "insider": [dict({k: v for k, v in r.items() if k != "_key"},
                                           flags=flag_insider(r, mine, watched)) for r in rows],
                          "notes": errors}, indent=2, ensure_ascii=False))
    else:
        if new_deals:
            print("NEW deals in your stocks or by investors you watch:")
            for d, f in new_deals:
                print("  " + deal_line(d, f))
        if rows:
            print("NEW insider disclosures (%d):" % len(rows))
            for r in rows:
                print("  " + insider_line(r, flag_insider(r, mine, watched)))
        if not new_deals and not rows:
            print("Nothing new%s." % ("" if a.read else " in the deals (add --read for insider disclosures)"))
        for e in errors:
            print("note: " + e)
    return 0 if (new_deals or rows) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Smart Money Tracker: NSE bulk and block deals, SEBI insider trades.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--symbol", help="one NSE symbol")
        p.add_argument("--client", help="part of an investor's name, e.g. kacholia")
        p.add_argument("--watchlist-file", default="trader/smartmoney.json", help="investors you watch")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--watch", default="trader/watchlist.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--archive", default="trader/state/deals", help="where each day's deal files are kept")
        p.add_argument("--no-archive", action="store_true")
        p.add_argument("--state", default="trader/state/smartmoney.json")
        p.add_argument("--max-read", type=int, default=25, help="insider XBRL files to read per run")
        p.add_argument("--show", type=int, default=40)
        p.add_argument("--offline", metavar="DIR")
        p.add_argument("--today", help="YYYY-MM-DD, for reproducible runs")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("deals", help="the latest bulk and block deals")
    common(p)
    p.add_argument("--watched", action="store_true", help="only investors in trader/smartmoney.json")
    p.add_argument("--hide-prop", action="store_true", help="leave out prop and HFT firms")
    p = sub.add_parser("insider", help="the latest insider-trading disclosures")
    common(p)
    p.add_argument("--promoters", action="store_true", help="promoters and promoter group only")
    p = sub.add_parser("new", help="only what is new since the last check (for /loop)")
    common(p)
    p.add_argument("--read", action="store_true", help="also read new insider disclosures")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    fetcher = Fetcher(offline=a.offline)
    mine = my_symbols(a.portfolio, a.watch, a.plan)
    watched = watched_names(a.watchlist_file)
    errors = []
    today = a.today or _today().isoformat()
    try:
        if a.cmd == "deals":
            return cmd_deals(a, fetcher, mine, watched, errors)
        if a.cmd == "insider":
            return cmd_insider(a, fetcher, mine, watched, errors)
        state = load_state(a.state)
        code = cmd_new(a, fetcher, mine, watched, errors, state, today)
        save_state(a.state, state, today)
        return code
    except DealsError as e:
        print("deals.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
