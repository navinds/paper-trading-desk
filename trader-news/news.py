#!/usr/bin/env python3
"""News monitor for trader-news. Standard library only.

Watches headlines and NSE filings for the stocks you watch or hold and prints
only what is new since the last check, newest first, with how far each stock
has moved today and a flag on anything that touches your symbols.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE TCS INFY
    python3 news.py --tickers RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-nse                         # headlines only
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Company names come from
trader/universe.txt (SYMBOL|Name, written by scan.py universe) when it is
there: they make the headline search and the NSE matching much better.

Sources:
  - Google News, India edition, one search per symbol (company name and symbol):
    https://news.google.com/rss/search?q=<query>&hl=en-IN&gl=IN&ceid=IN:en
  - NSE's own public RSS feeds, one request each for the whole market (the
    company's filings with the exchange, matched to your symbols):
      Online_announcements.xml   corporate announcements (results, board
                                 outcomes, director changes, ratings, deals)
      Board_Meetings.xml         board meetings ahead, with the purpose
                                 (financial results: the results date)
      Corporate_action.xml       ex-dates for dividends, bonus, splits
    https://nsearchives.nseindia.com/content/RSS/<feed>
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

Times print in India time (IST). State: trader/state/news.json remembers
what you have seen (30 days), so an item shows once. Offline: --offline DIR
reads gnews-<SYMBOL>.xml, chart-<SYMBOL>.json and nse-announcements.xml,
nse-board-meetings.xml, nse-corporate-actions.xml from DIR. The tests use it.

Headlines are leads, not facts: open the story or the filing before acting,
and treat nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_RSS = "https://nsearchives.nseindia.com/content/RSS/%s"
NSE_FEEDS = (("Online_announcements.xml", "nse-announcements.xml", "announcement"),
             ("Board_Meetings.xml", "nse-board-meetings.xml", "board meeting"),
             ("Corporate_action.xml", "nse-corporate-actions.xml", "corporate action"))
UA = "Mozilla/5.0 (compatible; india-trader-skill/1.0)"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
IST = _dt.timedelta(hours=5, minutes=30)

IMPORTANT = [  # NSE announcement subjects a day trader must read before a trade
    r"financial result", r"outcome of board meeting", r"board meeting", r"resignation", r"change in (?:director|key"
    r" managerial|management|auditor)", r"credit rating", r"pledge", r"acquisition", r"amalgamation|merger|scheme of "
    r"arrangement", r"fund ?rais|qualified institutions placement|preferential issue|rights issue", r"buy ?back",
    r"default", r"insolvency|nclt", r"fraud", r"suspension", r"open offer", r"offer for sale",
]
KEYWORDS = [
    ("earnings", r"\bresults?\b|\bQ[1-4]\b|\bnet profit\b|\bPAT\b|\bEBITDA\b|\brevenue\b|\bguidance\b|\bearnings\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\btarget price\b|\bprice target\b|\bbuy rating\b|\bsell rating\b"
                r"|\boverweight\b|\bunderweight\b|\bbrokerage\b"),
    ("legal", r"\bSEBI\b.*\b(?:order|probe|notice|penalty|ban)\b|\bprobe\b|\bED\b|\bCBI\b|\bcourt\b|\bNCLT\b"
              r"|\bpenalty\b|\braid\b|\bshow[- ]cause\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bstake\b|\bblock deal\b|\bbulk deal\b|\bOFS\b|\bIPO\b"
             r"|\bopen offer\b|\bdemerger\b"),
    ("capital", r"\bdividend\b|\bbonus\b|\bstock split\b|\bsplit\b|\bbuy ?back\b|\brights issue\b|\bQIP\b"
                r"|\bfund ?rais|\bpreferential\b|\bex-date\b|\brecord date\b"),
    ("people", r"\bCEO\b|\bMD\b|\bCFO\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\bdefault\b|\bpledge\b|\bfraud\b|\binsolvency\b|\bASM\b|\bGSM\b|\bdowngrade\b|\brating cut\b"
             r"|\bwarning\b|\bloss\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\bsuspen(?:d|sion)\b|\bupper circuit\b|\blower circuit\b|\bF&O ban\b|\bban period\b"),
    ("regulatory", r"\bRBI\b|\bSEBI\b|\bTRAI\b|\bDGCA\b|\bUSFDA\b|\bFDA\b|\bCDSCO\b|\bapproval\b|\bPLI\b|\blicen[cs]e\b"),
    ("macro", r"\bSensex\b|\bNifty\b|\brepo\b|\bMPC\b|\bGDP\b|\bCPI\b|\binflation\b|\bFIIs?\b|\bDIIs?\b|\bFPIs?\b"
              r"|\brupee\b|\bcrude\b|\bBudget\b|\bmonsoon\b|\bGST\b|\bFed\b|\btariffs?\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive India wall-clock time (IST, UTC+5:30)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def load_names(path="trader/universe.txt"):
    """{SYMBOL: company name} from scan.py's universe file (SYMBOL|Name a line)."""
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if "|" in ln and not ln.startswith("#"):
                    s, n = ln.rstrip("\n").split("|", 1)
                    out[s.strip().upper()] = n.strip()
    return out


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[sym.upper()] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(pl["symbol"].upper(), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[sym.upper()] = p.get("name") or out.get(sym.upper())
    return out


def short_name(name):
    """"Reliance Industries Limited" -> "Reliance Industries" (the words people search for)."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    n = re.sub(r"\b(?:limited|ltd\.?|ltd|private|pvt\.?)$", "", n, flags=re.I).strip(" .,")
    n = re.sub(r"\b(?:limited|ltd\.?)$", "", n, flags=re.I).strip(" .,")
    return n


def norm(name):
    """For matching an NSE feed title to a company name: lower case, no Limited, no punctuation."""
    n = short_name(name).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
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
            raise NewsError("HTTP %s for %s" % (exc.code, url.split("?")[0]))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url.split("/")[2], exc.reason))


def yahoo_symbol(sym):
    s = sym.strip().upper()
    if s.startswith("^") or s.endswith((".NS", ".BO")):
        return s
    return s + ".NS"


def gnews_query(sym, name):
    """A Google News search that finds the company: its name in quotes, plus the NSE symbol."""
    sn = short_name(name)
    q = '"%s" share' % sn if sn else '"%s" NSE share' % sym
    if sn and sn.upper() != sym:
        q = '"%s" OR "%s share"' % (sn, sym)
    return urllib.parse.quote(q)


# ---------------------------------------------------------------- parsing

def _text(el, name):
    c = el.find(name)
    return (c.text or "").strip() if c is not None and c.text else ""


def parse_time(value):
    """RFC 822 (RSS), ISO 8601, or NSE's "25-Oct-2026 20:13:42" (IST) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return (_dt.datetime.strptime(value, fmt) - IST).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_gnews(data, ticker):
    """Google News RSS -> items. Titles end with " - Publisher"."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("news for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        src = _text(it, "source")
        if src and title.endswith(" - " + src):
            title = title[:-len(src) - 3]
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "gn:" + guid, "source": src or "Google News", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": "", "link": link,
                    "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_nse_feed(data, kind):
    """One of NSE's RSS feeds -> raw items {company, link, description, time, kind}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("NSE %s feed is not valid XML: %s" % (kind, exc))
    out = []
    for it in root.iter("item"):
        title = " ".join(_text(it, "title").split())
        company = title.split(" - Ex-Date:")[0].strip() if kind == "corporate action" else title
        out.append({"company": company, "title": title, "link": _text(it, "link"),
                    "description": " ".join(_text(it, "description").split()),
                    "time": parse_time(_text(it, "pubDate")), "kind": kind})
    return out


def match_nse(raw, watch):
    """Match NSE feed items to your symbols by company name, or by the symbol that starts the filing's
    file name (NSE names many attachments SYMBOL_<date>...). -> items."""
    names = {t: norm(n) for t, n in watch.items() if n}
    out = []
    for r in raw:
        cn = norm(r["company"])
        base = os.path.basename(r["link"]).split("_")[0].upper()
        hits = sorted(t for t in watch if (names.get(t) and names[t] == cn) or base == t)
        if not hits:
            continue
        desc = r["description"]
        subject = desc.split("|SUBJECT:")[-1].strip() if "|SUBJECT:" in desc else desc
        if r["kind"] == "announcement":
            title = "%s: %s" % (r["company"], subject)
        elif r["kind"] == "board meeting":
            title = "%s board meeting: %s" % (r["company"], desc.replace(" |", ";"))
        else:
            title = "%s corporate action: %s" % (r["title"], desc.replace(" |", ";"))
        important = any(re.search(p, desc, re.I) for p in IMPORTANT) or r["kind"] != "announcement"
        uid = hashlib.sha1(("%s|%s|%s" % (r["link"], r["title"], desc)).encode("utf-8")).hexdigest()[:20]
        out.append({"id": "nse:" + uid, "source": "NSE filing", "kind": r["kind"], "ticker": hits[0],
                    "title": title, "summary": desc, "link": r["link"], "time": r["time"],
                    "important": important, "touches_nse": hits})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")   # previousClose is yesterday's, whatever the range
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            ys = urllib.parse.quote(yahoo_symbol(t))
            try:
                data = fetcher.get(CHART % ("query2", ys), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", ys), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [r"\b%s\b" % re.escape(t), r"\bNSE\s*:\s*%s\b" % re.escape(t)]
        sn = short_name(name)
        if sn and len(sn) >= 4:
            alts.append(r"\b%s\b" % re.escape(sn).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = set(it.get("touches_nse") or []) | ({it["ticker"]} if it["ticker"] in watch else set())
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "board meeting" and re.search(r"financial result", hay, re.I) and "earnings" not in it["tags"]:
            it["tags"].insert(0, "earnings")
        if it["kind"] == "corporate action" and "capital" not in it["tags"]:
            it["tags"].insert(0, "capital")
        it.setdefault("important", False)
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, nse=True, errors=None):
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            items += parse_gnews(fetcher.get(GNEWS % gnews_query(t, watch.get(t)), "gnews-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if nse:
        if not any(watch.values()):
            errors.append("no company names (run scan.py universe): NSE filings are matched by file name only")
        for feed, offline_name, kind in NSE_FEEDS:
            try:
                items += match_nse(parse_nse_feed(fetcher.get(NSE_RSS % feed, offline_name), kind), watch)
            except NewsError as exc:
                errors.append("NSE %s feed: %s" % (kind, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all. Board meetings and ex-dates
    are about days ahead, so they are kept whatever the window."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and it["time"] > ahead:
            continue
        if it["time"] is not None and it["time"] < cutoff and it["kind"] not in ("board meeting", "corporate action"):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s ₹%s %+.2f%% vs yesterday's close ₹%s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-16s %s" % (mark, when, who, it["source"][:16], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story or the filing before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols (default: watchlist + plan + portfolio)")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines, for company names")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-nse", action="store_true", help="headlines only, no NSE filings")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if args.tickers:
        watch = {t.upper(): known.get(t.upper()) for t in args.tickers}
    else:
        watch = known
    if not watch:
        print("news: no symbols. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    names = load_names(args.universe)
    watch = {t: (n or names.get(t)) for t, n in watch.items()}
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    if args.now and not re.search(r"(Z|[+-]\d\d:?\d\d)$", args.now.strip()):
        now = parse_time(args.now.strip().replace(" ", "T") + "+05:30")   # a bare --now is IST
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, nse=not args.no_nse, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
