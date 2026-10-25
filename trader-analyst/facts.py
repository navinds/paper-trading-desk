#!/usr/bin/env python3
"""Earnings reader for trader-analyst. Standard library only, no keys.

Pulls a company's reported quarterly numbers and lays out what changed,
quarter by quarter: revenue, gross, operating and net margins, net profit,
diluted EPS, and growth against the same quarter a year earlier (the Indian
fiscal year runs April to March: Q1 is April to June, Q4 is January to
March). It also looks for the company in NSE's filings feeds: a results
filing just posted, and a board meeting ahead that will consider results
(the next results date).

    python3 facts.py RELIANCE
    python3 facts.py RELIANCE TCS --quarters 8
    python3 facts.py RELIANCE --json
    python3 facts.py RELIANCE TCS HDFCBANK INFY --metrics trader/metrics.json

Sources:
  - The numbers: Yahoo Finance's fundamentals timeseries (unofficial and
    free, consolidated figures as Yahoo maps them; it can lag a filing by
    days and can miss a quarter):
    https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/<SYMBOL>.NS
  - NSE's public RSS feeds (the filings themselves, with the PDF or XBRL link):
    https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml
    https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml
  Company names for the NSE match come from trader/universe.txt
  (scan.py universe). Filed numbers win: when the NSE filing is newer than
  Yahoo's last quarter, read the filing before you sum up.

Amounts print in Rs crore (1 crore = 10 million). A quarter Yahoo does not
have is shown as missing, never guessed; the last-four-quarters line says so
when one of the four is missing.

--metrics FILE merges one row per symbol into a JSON file (for notes or a
sheet). Offline: --offline DIR reads ts-<SYMBOL>.json,
nse-financial-results.xml and nse-board-meetings.xml from DIR.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime as _dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TS_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
          "?type=%s&period1=%d&period2=%d&merge=false")
NSE_RSS = "https://nsearchives.nseindia.com/content/RSS/%s"
UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"
CRORE = 1e7
FIELDS = (("revenue", "TotalRevenue"), ("gross_profit", "GrossProfit"), ("operating_income", "OperatingIncome"),
          ("net_income", "NetIncome"), ("eps_diluted", "DilutedEPS"))
IST = _dt.timedelta(hours=5, minutes=30)


class FactsError(Exception):
    pass


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise FactsError("HTTP %s for %s" % (exc.code, url.split("?")[0]))
        except urllib.error.URLError as exc:
            raise FactsError("could not reach %s: %s" % (url.split("/")[2], exc.reason))


def yahoo_symbol(sym):
    s = sym.strip().upper()
    if s.startswith("^") or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def ts_url(sym, now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    types = ",".join("quarterly" + f for _, f in FIELDS) + ",annualTotalRevenue,annualNetIncome,annualDilutedEPS"
    p2 = int(now.timestamp()) + 86400 * 30
    return TS_URL % (urllib.parse.quote(yahoo_symbol(sym)), types, p2 - 86400 * 365 * 4, p2)


def load_names(path="trader/universe.txt"):
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if "|" in ln:
                    s, n = ln.rstrip("\n").split("|", 1)
                    out[s.strip().upper()] = n.strip()
    return out


# ---------------------------------------------------------------- the numbers

def parse_timeseries(data, sym):
    """Yahoo timeseries JSON -> {"quarterly": {field: {end: value}}, "annual": {...}, "currency": ...}."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        res = doc["timeseries"]["result"]
    except (ValueError, KeyError, TypeError):
        raise FactsError("Yahoo's fundamentals for %s are not the shape this tool knows" % sym)
    out = {"quarterly": {}, "annual": {}, "currency": None}
    names = dict((f, k) for k, f in FIELDS)
    for r in res or []:
        t = ((r.get("meta") or {}).get("type") or [""])[0]
        m = re.match(r"^(quarterly|annual)(.+)$", t)
        if not m or m.group(2) not in names:
            continue
        key = names[m.group(2)]
        vals = {}
        for v in r.get(t) or []:
            if not v or not v.get("asOfDate"):
                continue
            raw = (v.get("reportedValue") or {}).get("raw")
            if raw is None:
                continue
            vals[v["asOfDate"]] = float(raw)
            out["currency"] = out["currency"] or v.get("currencyCode")
        out[m.group(1)][key] = vals
    if not any(out["quarterly"].values()):
        raise FactsError("Yahoo has no quarterly numbers for %s (check the symbol; new listings, SME and "
                         "some banks have none)" % sym)
    return out


def fiscal_label(end):
    """2026-06-30 -> "Q1 FY27" (India: April to March)."""
    d = _dt.date.fromisoformat(end)
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(d.month)
    fy = d.year + 1 if d.month > 3 else d.year
    return ("Q%d FY%02d" % (q, fy % 100)) if q else d.strftime("%b %Y")


def quarter_ends(last, n):
    """The n calendar quarter ends up to `last`, newest first (2026-06-30, 2026-03-31, ...)."""
    d = _dt.date.fromisoformat(last)
    out = []
    y, m = d.year, d.month - d.month % 3      # the quarter end on or before `last`
    if m == 0:
        y, m = y - 1, 12
    if (m == d.month) and d.day < (30 if m in (6, 9) else 31):
        m -= 3
        if m <= 0:
            y, m = y - 1, m + 12
    for _ in range(n):
        out.append(_dt.date(y, m, 30 if m in (6, 9) else 31).isoformat())
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return out


def due_quarter(today):
    """The newest quarter whose results must be out by today: SEBI's listing rules give companies 45
    days after a quarter ends, 60 for the March quarter (the audited full year)."""
    for end in quarter_ends(today.isoformat(), 8):
        d = _dt.date.fromisoformat(end)
        if d + _dt.timedelta(days=60 if d.month == 3 else 45) <= today:
            return end
    return None


def stale_note(result, today):
    due = due_quarter(today)
    if due and result["last"] < due:
        return ("Yahoo's numbers stop at %s (%s), but %s results were due by now (45 days after the quarter, 60 "
                "for March): read the company's latest results on NSE before you sum up."
                % (result["last"], fiscal_label(result["last"]), fiscal_label(due)))
    return None


def pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(part / whole * 100, 1)


def analyse(ts, quarters=6):
    """Rows for the last `quarters` calendar quarters (missing ones included), TTM and flags."""
    q = ts["quarterly"]
    ends = sorted(set(e for vals in q.values() for e in vals))
    if not ends:
        raise FactsError("no quarters")
    last = ends[-1]
    want = quarter_ends(last, quarters)
    rows = []
    for end in want:
        r = {"end": end, "fiscal": fiscal_label(end)}
        for key, _ in FIELDS:
            r[key] = q.get(key, {}).get(end)
        r["missing"] = all(r[k] is None for k, _ in FIELDS)
        prev_year = quarter_ends(end, 5)[4]
        prev_q = quarter_ends(end, 2)[1]
        r["revenue_yoy"] = pct(r["revenue"], q.get("revenue", {}).get(prev_year))
        r["net_income_yoy"] = pct(r["net_income"], q.get("net_income", {}).get(prev_year))
        r["eps_yoy"] = pct(r["eps_diluted"], q.get("eps_diluted", {}).get(prev_year))
        r["revenue_qoq"] = pct(r["revenue"], q.get("revenue", {}).get(prev_q))
        r["gross_margin"] = margin(r["gross_profit"], r["revenue"])
        r["operating_margin"] = margin(r["operating_income"], r["revenue"])
        r["net_margin"] = margin(r["net_income"], r["revenue"])
        rows.append(r)
    four = quarter_ends(last, 4)
    ttm = {}
    gaps = [e for e in four if q.get("revenue", {}).get(e) is None]
    for key in ("revenue", "net_income", "eps_diluted"):
        vals = [q.get(key, {}).get(e) for e in four]
        ttm[key] = round(sum(vals), 2) if all(v is not None for v in vals) else None
    ttm["missing"] = gaps
    return {"last": last, "rows": rows, "ttm": ttm, "currency": ts.get("currency") or "INR",
            "annual": ts.get("annual") or {}}


def changes(result):
    """Plain sentences on the newest quarter."""
    rows = [r for r in result["rows"] if not r["missing"]]
    out = []
    if not rows:
        return ["No quarter has numbers."]
    r = rows[0]
    bits = []
    if r["revenue"] is not None:
        bits.append("revenue %s%s" % (cr(r["revenue"]), (" (%+.1f%% on a year earlier)" % r["revenue_yoy"])
                                      if r["revenue_yoy"] is not None else " (no year-earlier quarter to compare)"))
    if r["net_income"] is not None:
        bits.append("net profit %s%s" % (cr(r["net_income"]), (" (%+.1f%%)" % r["net_income_yoy"])
                                         if r["net_income_yoy"] is not None else ""))
    if r["eps_diluted"] is not None:
        bits.append("diluted EPS %s %.2f%s" % ("Rs" if _unit[0] == "INR" else "US$", r["eps_diluted"],
                                               (" (%+.1f%%)" % r["eps_yoy"])
                                               if r["eps_yoy"] is not None else ""))
    out.append("%s (quarter to %s): %s." % (r["fiscal"], r["end"], ", ".join(bits)))
    prev = rows[1] if len(rows) > 1 else None
    if r["operating_margin"] is not None:
        line = "operating margin %.1f%%" % r["operating_margin"]
        if prev and prev["operating_margin"] is not None:
            line += " (%+.1f points on %s)" % (r["operating_margin"] - prev["operating_margin"], prev["fiscal"])
        if r["gross_margin"] is not None:
            line = "gross margin %.1f%%, " % r["gross_margin"] + line
        out.append(line[:1].upper() + line[1:] + ".")
    else:
        out.append("No operating margin: banks, NBFCs and insurers report differently (blank, not zero).")
    if r["net_income"] is not None and r["operating_income"] is not None and r["net_income"] > r["operating_income"]:
        out.append("Net profit is above operating profit: other income or an exceptional gain is inside it. "
                   "Read the filing's notes before you call it growth.")
    if r["net_income"] is not None and r["net_income"] < 0:
        out.append("A loss this quarter.")
    missing = [x["fiscal"] for x in result["rows"] if x["missing"]]
    if missing:
        out.append("Yahoo has no numbers for %s: check the NSE filing for those quarters." % ", ".join(missing))
    return out


# ---------------------------------------------------------------- NSE filings

def parse_rss(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FactsError("NSE feed is not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        def t(n):
            c = it.find(n)
            return " ".join((c.text or "").split()) if c is not None and c.text else ""
        out.append({"company": t("title"), "link": t("link"), "description": t("description"), "date": t("pubDate")})
    return out


def _norm(name):
    n = re.sub(r"\b(?:limited|ltd\.?)\b", "", str(name or ""), flags=re.I).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def nse_filings(sym, name, results_feed, board_feed):
    """The company's items in NSE's results and board-meeting feeds."""
    want = _norm(name) if name else None

    def mine(r):
        base = os.path.basename(r["link"]).split("_")[0].upper()
        return (want and _norm(r["company"]) == want) or base == sym
    results = [dict(r, period=_field(r["description"], "PERIOD ENDED"),
                    consolidated=_field(r["description"], "CONSOLIDATED/NON-CONSOLIDATED"))
               for r in results_feed if mine(r)]
    boards = [dict(r, meeting=_field(r["description"], "Meeting Date"))
              for r in board_feed if mine(r) and re.search(r"financial result", r["description"], re.I)]
    return {"results": results, "board_meetings": boards}


def _field(desc, key):
    m = re.search(r"%s\s*:\s*([^|]+)" % re.escape(key), desc, re.I)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------- output

UNIT = {"INR": (CRORE, "Rs %s cr", "Rs crore"), "USD": (1e6, "US$ %s million", "US$ million")}
_unit = ["INR"]   # set per company by render(): Yahoo files some Indian companies in US$


def cr(v):
    """Rupees -> "Rs 30,946.8 cr" (or US$ millions for a company Yahoo reports in dollars)."""
    div, fmt, _ = UNIT.get(_unit[0], UNIT["INR"])
    return fmt % "{:,.1f}".format(v / div)


def _c(v):
    div = UNIT.get(_unit[0], UNIT["INR"])[0]
    return "-" if v is None else "{:,.0f}".format(v / div)


def _p(v):
    return "-" if v is None else "%+.1f%%" % v


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(sym, name, result, filings, today=None):
    cur = (result.get("currency") or "INR").upper()
    _unit[0] = cur if cur in UNIT else "INR"
    out = ["%s%s. %s except EPS%s. Source: Yahoo Finance fundamentals (unofficial; consolidated as Yahoo "
           "maps them)." % (sym, (" (%s)" % name) if name else "", UNIT[_unit[0]][2],
                            " (in %s)" % cur if cur != "INR" else ""), ""]
    if cur != "INR":
        out.insert(1, "NOTE: Yahoo reports %s in %s (the figures it files in the US, under US GAAP or IFRS), not "
                      "in rupees. For the Ind AS rupee numbers read the NSE results filing." % (sym, cur))
    out.append("%-11s %-8s %10s %8s %7s %7s %7s %9s %8s %7s" % (
        "quarter end", "fiscal", "revenue", "rev yoy", "gross", "oper", "net", "net prof", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        if r["missing"]:
            out.append("%-11s %-8s %s" % (r["end"], r["fiscal"], "   missing in Yahoo's data"))
            continue
        out.append("%-11s %-8s %10s %8s %7s %7s %7s %9s %8s %7s" % (
            r["end"], r["fiscal"], _c(r["revenue"]), _p(r["revenue_yoy"]), _pm(r["gross_margin"]),
            _pm(r["operating_margin"]), _pm(r["net_margin"]), _c(r["net_income"]),
            "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"], _p(r["eps_yoy"])))
    t = result["ttm"]
    out.append("")
    if t["missing"]:
        out.append("Last 4 quarters: not summed, %s missing in Yahoo's data." % ", ".join(fiscal_label(e) for e in t["missing"]))
    else:
        out.append("Last 4 quarters: revenue %s, net profit %s, diluted EPS %s." % (
            cr(t["revenue"]), cr(t["net_income"]) if t["net_income"] is not None else "-",
            ("%s %.2f" % ("Rs" if _unit[0] == "INR" else "US$", t["eps_diluted"]))
            if t["eps_diluted"] is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    note = stale_note(result, today or _dt.date.today())
    if note:
        out.append("")
        out.append("NOTE: " + note)
    out.append("")
    if filings is None:
        out.append("NSE filings: not checked.")
    else:
        for r in filings["results"][:3]:
            out.append("NSE results filing, posted %s: %s  %s" % (r["date"], r["description"][:140], r["link"]))
            if r.get("period"):
                try:
                    p = _dt.datetime.strptime(r["period"], "%d-%b-%Y").date().isoformat()
                    if p > result["last"]:
                        out.append("NOTE: this filing (period %s) is newer than Yahoo's last quarter %s: read it "
                                   "before summing up." % (p, result["last"]))
                except ValueError:
                    pass
        for b in filings["board_meetings"][:2]:
            out.append("Board meeting %s to consider financial results (NSE filing, %s)  %s"
                       % (b.get("meeting") or "?", b["date"], b["link"]))
        if not filings["results"] and not filings["board_meetings"]:
            out.append("NSE's feeds have no results filing or results board meeting for %s right now (they hold the "
                       "last few days only). For the next results date: /trader-news or the company's investor page."
                       % sym)
    return "\n".join(out) + "\n"


def metrics_row(sym, result):
    r = next((x for x in result["rows"] if not x["missing"]), {})
    t = result["ttm"]
    return {"symbol": sym, "quarter_end": r.get("end"), "fiscal": r.get("fiscal"),
            "ttm_revenue_cr": round(t["revenue"] / CRORE, 1) if t.get("revenue") else None,
            "ttm_net_income_cr": round(t["net_income"] / CRORE, 1) if t.get("net_income") else None,
            "ttm_eps_diluted": t.get("eps_diluted"), "revenue_yoy_pct": r.get("revenue_yoy"),
            "eps_yoy_pct": r.get("eps_yoy"), "operating_margin_pct": r.get("operating_margin"),
            "net_margin_pct": r.get("net_margin"), "ttm_missing": [fiscal_label(e) for e in t.get("missing", [])]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="+", help="NSE symbols, e.g. RELIANCE TCS")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines, for the NSE match")
    ap.add_argument("--no-nse", action="store_true", help="skip NSE's results and board-meeting feeds")
    ap.add_argument("--metrics", metavar="FILE", help="merge one row per symbol into this JSON file")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--today", help="YYYY-MM-DD, for reproducible runs (default: today)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    today = _dt.date.fromisoformat(args.today) if args.today else (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + IST).date()
    if not 1 <= args.quarters <= 16:
        print("facts: --quarters is 1 to 16", file=sys.stderr)
        return 2
    fetcher = Fetcher(offline=args.offline)
    names = load_names(args.universe)
    feeds = None
    if not args.no_nse:
        try:
            feeds = (parse_rss(fetcher.get(NSE_RSS % "Financial_Results.xml", "nse-financial-results.xml")),
                     parse_rss(fetcher.get(NSE_RSS % "Board_Meetings.xml", "nse-board-meetings.xml")))
        except FactsError as exc:
            print("facts: NSE feeds: %s (continuing without them)" % exc, file=sys.stderr)
    results, failed = {}, 0
    for sym in [s.upper() for s in args.symbols]:
        try:
            ts = parse_timeseries(fetcher.get(ts_url(sym), "ts-%s.json" % sym), sym)
            result = analyse(ts, args.quarters)
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue
        filings = nse_filings(sym, names.get(sym), *feeds) if feeds else None
        results[sym] = {"result": result, "filings": filings, "name": names.get(sym),
                        "stale_note": stale_note(result, today)}
        if not args.json:
            sys.stdout.write(render(sym, names.get(sym), result, filings, today))
            if len(args.symbols) > 1:
                print("")
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.metrics and results:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r["symbol"]: r for r in json.load(fh).get("rows", [])}
        for sym, r in results.items():
            existing[sym] = metrics_row(sym, r["result"])
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"source": "Yahoo Finance fundamentals (unofficial)", "currency": "INR",
                       "rows": [existing[k] for k in sorted(existing)]}, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 1 if failed and not results else 0


if __name__ == "__main__":
    sys.exit(main())
