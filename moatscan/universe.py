"""Whole-market scan using SEC's bulk XBRL file (every filer's company facts in one zip).

Pass 1 (no prices): parse every exchange-listed company, keep those that pass the
          business-quality bar and have few accounting red flags.
Pass 2: drop financials/REITs (SIC 6000-6799), fetch prices in batches, value survivors,
          check insider buying for the best candidates.
Also tags Graham "net-nets" (price below net current assets) - the cigar butts Buffett
bought when he ran small money.
"""
from __future__ import annotations

import json
import math
import os
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from .analysis import Result, analyze
from .fundamentals import annual_table, shares_outstanding

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
EXCH_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

_ZIP = None
_CFG = None


def download_bulk(sec, path: Path, max_age_days: float = 7) -> Path:
    if path.exists() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    print("Downloading SEC bulk company facts (~1.3 GB, once a week)...", flush=True)
    with sec.s.get(BULK_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done/1e6:,.0f} / {total/1e6:,.0f} MB", end="", flush=True)
    print()
    tmp.rename(path)
    return path


def listed_companies(sec, include_otc: bool) -> dict[int, tuple[str, str, str]]:
    m = sec._cached_json("company_tickers_exchange.json", EXCH_URL, 24 * 7)
    f = m["fields"]
    out: dict[int, tuple[str, str, str]] = {}
    ok = {"NYSE", "Nasdaq", "NYSE American", "NYSE Arca", "CBOE"}
    for row in m["data"]:
        d = dict(zip(f, row))
        ex = d.get("exchange") or ""
        if not include_otc and ex not in ok:
            continue
        cik = int(d["cik"])
        if cik not in out:  # first listed class only
            out[cik] = (d["ticker"], d["name"], ex)
    return out


def _init(zpath, cfg):
    global _ZIP, _CFG
    _ZIP = zipfile.ZipFile(zpath)
    _CFG = cfg


def _pass1(args):
    name, cik, ticker, title = args
    try:
        facts = json.loads(_ZIP.read(name))
        df = annual_table(facts)
        if df.empty or df["revenue"].notna().sum() < 5:
            return None
        if df.index.max() < pd.Timestamp.now().year - 2:  # stale filer
            return None
        r = analyze(ticker, title, df, shares_outstanding(facts), None, _CFG, 0.043)
        nflags = sum(1 for c in r.flags if c.passed)
        last = df.iloc[-1]
        ncav = (last.get("cur_assets", math.nan) or math.nan) - (last.get("liabilities", math.nan) or math.nan)
        bar = min(_CFG["verdict"]["min_quality"], _CFG.get("scan", {}).get("small_min_quality", 100))
        keep = (r.quality_score >= bar and nflags <= _CFG["verdict"]["max_flags"])
        netnet_cand = ncav == ncav and ncav > 0 and last["net_income"] > 0
        if not (keep or netnet_cand):
            return None
        return {"cik": cik, "ticker": ticker, "title": title, "df": df.to_json(), "shares": shares_outstanding(facts),
                "quality": r.quality_score, "keep": keep, "ncav": ncav}
    except Exception:  # noqa: BLE001
        return None


def batch_prices(tickers: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        import yfinance as yf
    except ImportError:
        return out
    for i in range(0, len(tickers), 150):
        chunk = tickers[i:i + 150]
        try:
            px = yf.download(chunk, period="5d", progress=False, threads=True, auto_adjust=False)["Close"]
            if isinstance(px, pd.Series):
                px = px.to_frame(chunk[0])
            last = px.ffill().iloc[-1]
            for t, v in last.items():
                if v == v and v > 0:
                    out[str(t)] = float(v)
        except Exception:  # noqa: BLE001
            continue
        print(f"\r  prices {min(i + 150, len(tickers))}/{len(tickers)}", end="", flush=True)
    print()
    # Yahoo rate-limits big batches: retry misses slowly, one at a time
    missing = [t for t in tickers if t not in out]
    if missing:
        print(f"  retrying {len(missing)} missing prices...", flush=True)
        from .prices import price
        for t in missing:
            time.sleep(0.4)
            p = price(t)
            if p:
                out[t] = p
    return out


def scan(sec, cfg: dict, treasury: float, include_otc=False, workers=None, min_cap=None, max_cap=None,
         insiders_top=40) -> tuple[list[Result], pd.DataFrame]:
    from .insiders import insider_summary
    sc = cfg.get("scan", {})
    min_cap = sc.get("min_market_cap", 30e6) if min_cap is None else min_cap
    max_cap = sc.get("max_market_cap", float("inf")) if max_cap is None else max_cap
    zpath = download_bulk(sec, Path(cfg["sec"]["cache_dir"]).parent / "bulk" / "companyfacts.zip")
    listed = listed_companies(sec, include_otc)
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
    jobs = []
    for n in names:
        try:
            cik = int(n.replace("CIK", "").replace(".json", ""))
        except ValueError:
            continue
        if cik in listed:
            t, title, _ = listed[cik]
            jobs.append((n, cik, t, title))
    print(f"Pass 1: business quality on {len(jobs):,} listed companies...", flush=True)
    survivors = []
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 4) - 1),
                             initializer=_init, initargs=(str(zpath), cfg)) as ex:
        for i, r in enumerate(ex.map(_pass1, jobs, chunksize=20), 1):
            if r:
                survivors.append(r)
            if i % 250 == 0 or i == len(jobs):
                print(f"\r  {i:,}/{len(jobs):,} parsed, {len(survivors)} candidates", end="", flush=True)
    print()

    # drop financials / REITs (model doesn't fit them)
    print("Pass 2: industry check, prices, valuation...", flush=True)
    kept = []
    for s in survivors:
        try:
            sub = sec.submissions(s["cik"])
            sic = int(sub.get("sic") or 0)
            forms = set(sub.get("filings", {}).get("recent", {}).get("form", [])[:300])
        except Exception:  # noqa: BLE001
            sic, forms = 0, set()
        if 6000 <= sic <= 6799:
            continue
        if forms & {"20-F", "40-F"}:
            continue  # foreign ADRs: share/ADS ratios and currencies break per-share math
        s["sic"] = sic
        kept.append(s)
    prices = batch_prices([s["ticker"].replace(".", "-") for s in kept])

    results: list[Result] = []
    rows = []
    for s in kept:
        px = prices.get(s["ticker"].replace(".", "-"))
        sh = s["shares"]
        if not sh:
            js = json.loads(s["df"]).get("shares_diluted", {})
            vals = [v for _, v in sorted(js.items()) if v]
            sh = vals[-1] if vals else None
        if not px or not sh:
            continue
        js = json.loads(s["df"]).get("shares_diluted", {})
        dl = [v for _, v in sorted(js.items()) if v]
        if dl and not 0.75 <= sh / dl[-1] <= 1.33:
            sh = dl[-1]  # same guard the valuation uses, so market cap and value agree
        mcap = px * sh
        if not (min_cap <= mcap <= max_cap):
            continue
        df = pd.read_json(__import__("io").StringIO(s["df"]))
        df.index = df.index.astype(int)
        df.index.name = "fy"
        small = mcap < sc.get("small_cap_max", 3e9)
        keep = s["keep"] and (small or s["quality"] >= cfg["verdict"]["min_quality"])
        ccfg = cfg
        if small:  # small caps: shorter histories and lumpier numbers, so a slightly lower quality bar
            ccfg = {**cfg, "verdict": {**cfg["verdict"], "min_quality": sc.get("small_min_quality", cfg["verdict"]["min_quality"])}}
        fg = None
        if keep:
            from .prices import analyst_growth
            fg = analyst_growth(s["ticker"].replace(".", "-"))
        r = analyze(s["ticker"], s["title"], df, sh, px, ccfg, treasury, sic=s["sic"], fwd_growth=fg)
        netnet = s["ncav"] == s["ncav"] and s["ncav"] > 0 and mcap < s["ncav"] * cfg.get("scan", {}).get("netnet_ratio", 0.67)
        if netnet:
            r.notes.append(f"Graham net-net: market cap {mcap/1e6:,.0f}M vs net current assets {s['ncav']/1e6:,.0f}M "
                           "(cigar butt - cheap on assets, not a quality compounder).")
        if not keep and not netnet:
            continue
        r.size = _size(mcap)
        results.append(r)
        v = r.valuation
        rows.append({"ticker": r.ticker, "name": r.name, "size": r.size, "market_cap": mcap, "price": px,
                     "verdict": r.verdict, "quality": round(r.quality_score, 1), "iv_per_share": v.get("iv_per_share"),
                     "buy_below": v.get("buy_below"), "margin_of_safety": v.get("margin_of_safety"),
                     "implied_growth": v.get("implied_growth"), "hist_growth": v.get("hist_growth"),
                     "recent_growth": v.get("recent_growth"), "analyst_growth": v.get("analyst_growth"), "growth_used": v.get("growth_used"),
                     "iv_no_growth": v.get("iv_no_growth"), "mos_no_growth": v.get("mos_no_growth"),
                     "oe_yield": v.get("oe_yield"), "ev_ebit": v.get("ev_ebit"), "piotroski": r.piotroski,
                     "red_flags": sum(1 for c in r.flags if c.passed), "net_net": netnet, "sic": s["sic"],
                     "small": small})
    table = pd.DataFrame(rows)
    if table.empty:
        return results, table
    table = table.sort_values("margin_of_safety", ascending=False)

    good = table.verdict.isin(["BUY ZONE", "WATCHLIST"])
    small_good = table[good & table.small].ticker.tolist()
    top = small_good + [t for t in table[good].ticker if t not in small_good][: max(0, insiders_top - len(small_good))]
    top += [t for t in table[table.net_net].ticker if t not in top]

    # under-the-radar signals for small caps worth a look
    print(f"Radar check on {len(small_good)} small caps...", flush=True)
    from .prices import radar
    rad = {t: radar(t.replace(".", "-")) for t in small_good}
    for col in ("analysts", "inst_own", "insider_own", "off_high"):
        table[col] = table.ticker.map(lambda t, c=col: rad.get(t, {}).get(c))
    print(f"Insider check on top {len(top)}...", flush=True)
    by_t = {r.ticker: r for r in results}
    cik_of = {s["ticker"]: s["cik"] for s in kept}
    for t in top:
        try:
            by_t[t].insiders = insider_summary(sec, cik_of[t], cfg)
        except Exception:  # noqa: BLE001
            pass
    table["insider_signal"] = table.ticker.map(lambda t: by_t[t].insiders.get("cluster", "") if t in by_t else "")
    table["gem_score"] = table.apply(gem_score, axis=1)
    for r in results:
        r.radar = rad.get(r.ticker, {})
    return results, table


def gem_score(r) -> float | None:
    """Contrarian small-cap score, 0-100: cheap even with no growth, quality, few analysts watching,
    beaten down, insiders own a lot or are buying."""
    if not r.get("small") or r.get("verdict") not in ("BUY ZONE", "WATCHLIST"):
        return None
    def nz(x, d=0.0):
        return d if x is None or x != x else float(x)
    mos0 = max(min(nz(r.get("mos_no_growth"), -0.5), 0.6), -0.5)      # -0.5..0.6
    sc = 30 * (mos0 + 0.5) / 1.1
    sc += 20 * (nz(r.get("quality"), 60) - 60) / 40
    a = r.get("analysts")
    sc += 15 if a is None or a != a or a <= 2 else 10 if a <= 5 else 4 if a <= 8 else 0
    sc += 10 * min(max(nz(r.get("off_high")), 0), 0.6) / 0.6          # down from 52w high
    sc += 10 * min(nz(r.get("insider_own")), 0.3) / 0.3
    sc += 10 if str(r.get("insider_signal")) == "True" else 0
    sc += 5 if r.get("verdict") == "BUY ZONE" else 0
    return round(max(min(sc, 100), 0), 1)


def _size(mcap: float) -> str:
    return ("micro" if mcap < 300e6 else "small" if mcap < 2e9 else "mid" if mcap < 10e9 else "large")
