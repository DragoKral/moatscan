"""MoatScan command line.

  python -m moatscan analyze MU KO AAPL          # deep read of a few companies
  python -m moatscan analyze MU --price 95       # override price
  python -m moatscan screen                      # whole universe in config.yaml
  python -m moatscan screen --no-insiders        # faster
  python -m moatscan scan                        # whole US market incl. small/micro caps
  python -m moatscan scan --max-cap 2e9          # small + micro caps only
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import yaml

from . import prices
from .analysis import Result, analyze
from .fundamentals import annual_table, shares_outstanding
from .insiders import insider_summary
from .report import render
from .sec import SEC


def load_cfg(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def run_one(sec: SEC, t: str, cfg: dict, treasury: float, price_override=None, insiders=True) -> Result:
    cik, name = sec.cik(t)
    facts = sec.company_facts(cik)
    df = annual_table(facts)
    px = price_override if price_override is not None else prices.price(t)
    try:
        sic = sec.submissions(cik).get("sic")
    except Exception:  # noqa: BLE001
        sic = None
    res = analyze(t.upper(), name, df, shares_outstanding(facts), px, cfg, treasury, sic=sic)
    if insiders and res.verdict != "OUTSIDE CIRCLE":
        try:
            res.insiders = insider_summary(sec, cik, cfg)
        except Exception as e:  # noqa: BLE001
            res.notes.append(f"Insider check failed: {e}")
    return res


def _line(r: Result) -> str:
    v = r.valuation or {}
    mos = v.get("margin_of_safety")
    q = "" if math.isnan(r.quality_score) else f"Q{r.quality_score:3.0f}"
    m = "" if mos is None or (isinstance(mos, float) and math.isnan(mos)) else f"MoS {mos:+.0%}"
    iv = v.get("iv_per_share")
    ivs = "" if iv is None else f"IV ${iv:,.0f} vs ${v.get('price') or 0:,.0f}"
    ins = " ⚑insiders" if r.insiders.get("cluster") else ""
    return f"{r.ticker:6} {r.verdict:22} {q:5} {m:10} {ivs}{ins}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="moatscan")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("tickers", nargs="+")
    a.add_argument("--price", type=float, help="override price (single ticker)")
    a.add_argument("--no-insiders", action="store_true")
    a.add_argument("--no-open", action="store_true")
    s = sub.add_parser("screen")
    s.add_argument("--no-insiders", action="store_true")
    s.add_argument("--no-open", action="store_true")
    u = sub.add_parser("scan", help="whole market from SEC bulk data")
    u.add_argument("--min-cap", type=float)
    u.add_argument("--max-cap", type=float)
    u.add_argument("--include-otc", action="store_true")
    u.add_argument("--workers", type=int)
    u.add_argument("--cards", type=int, default=80, help="detailed cards in HTML report")
    u.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_cfg(args.config)
    sec = SEC(os.environ.get("SEC_USER_AGENT") or cfg["sec"]["user_agent"], cfg["sec"]["cache_dir"])
    treasury = prices.treasury_10y(cfg["valuation"]["default_treasury"])
    if args.cmd == "scan":
        return _scan(args, sec, cfg, treasury)
    tickers = args.tickers if args.cmd == "analyze" else cfg["universe"]
    results: list[Result] = []
    for i, t in enumerate(tickers, 1):
        try:
            po = args.price if (args.cmd == "analyze" and args.price and len(tickers) == 1) else None
            r = run_one(sec, t, cfg, treasury, po, insiders=not args.no_insiders)
        except Exception as e:  # noqa: BLE001
            r = Result(ticker=t.upper(), name="", years=[], table=__import__("pandas").DataFrame(),
                       verdict="ERROR", verdict_why=str(e))
            if os.environ.get("MOATSCAN_DEBUG"):
                traceback.print_exc()
        results.append(r)
        print(f"[{i}/{len(tickers)}] {_line(r)}", flush=True)

    out = Path("reports")
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    html_path = out / f"moatscan_{args.cmd}_{stamp}.html"
    html_path.write_text(render(results, "MoatScan " + ("screen" if args.cmd == "screen" else " · ".join(tickers).upper())))
    with open(out / f"moatscan_{args.cmd}_{stamp}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "verdict", "quality", "price", "iv_per_share", "buy_below", "margin_of_safety",
                    "implied_growth", "oe_yield", "piotroski", "beneish", "red_flags", "insider_signal"])
        for r in results:
            v = r.valuation or {}
            w.writerow([r.ticker, r.verdict, round(r.quality_score, 1) if not math.isnan(r.quality_score) else "",
                        v.get("price"), v.get("iv_per_share"), v.get("buy_below"), v.get("margin_of_safety"),
                        v.get("implied_growth"), v.get("oe_yield"), r.piotroski, r.beneish,
                        sum(1 for c in r.flags if c.passed), r.insiders.get("cluster", "")])
    print(f"\nReport: {html_path.resolve()}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


def _scan(args, sec, cfg, treasury):
    from .universe import scan
    from .report import ORDER
    results, table = scan(sec, cfg, treasury, include_otc=args.include_otc, workers=args.workers,
                          min_cap=args.min_cap, max_cap=args.max_cap)
    out = Path("reports")
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = out / f"moatscan_scan_{stamp}.csv"
    table.to_csv(csv_path, index=False)
    ranked = sorted(results, key=lambda r: (ORDER.get(r.verdict, 6), -(r.valuation.get("margin_of_safety") or -9)))
    best = [r for r in ranked if r.verdict in ("BUY ZONE", "WATCHLIST")]
    nn = [r for r in ranked if any("net-net" in n for n in r.notes) and r not in best]
    rest = [r for r in ranked if r not in best and r not in nn]
    show = (best + nn + rest)[: args.cards]
    html_path = out / f"moatscan_scan_{stamp}.html"
    html_path.write_text(render(show, f"MoatScan market scan · {len(results)} candidates"))
    if not table.empty:
        print("\nVerdicts:", table.verdict.value_counts().to_dict())
        print("By size:", table["size"].value_counts().to_dict())
        print(f"Net-nets: {int(table.net_net.sum())}")
        for r in show[:25]:
            print("  " + _line(r) + f"  [{getattr(r, 'size', '')}]")
    print(f"\nReport: {html_path.resolve()}\nFull table: {csv_path.resolve()}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
