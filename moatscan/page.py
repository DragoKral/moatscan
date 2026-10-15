"""Builds the 'MoatScan Market Scan' summary page from a scan CSV.

  python -m moatscan.page reports/moatscan_scan_YYYYMMDD_HHMM.csv [out.html] [--prev older.csv]
"""
from __future__ import annotations

import glob
import html
import sys
from datetime import datetime

import pandas as pd

from .analysis import is_cyclical_sic

CSS = """
/* Layout: single reading column, funnel summary up top, ranked rows grouped by thesis */
:root{--bg:#f6f7f4;--surface:#ffffff;--fg:#1b211d;--mute:#5d665f;--line:#dde2dc;--accent:#2f6b4f;--good:#1f7a4d;--warn:#9a5b00;
--display:"Fraunces",Georgia,serif;--body:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121513;--surface:#1a1e1b;--fg:#e8ece8;--mute:#9aa49c;--line:#2b312d;--accent:#7fc4a0;--good:#6fd09c;--warn:#e3a54a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121513;--surface:#1a1e1b;--fg:#e8ece8;--mute:#9aa49c;--line:#2b312d;--accent:#7fc4a0;--good:#6fd09c;--warn:#e3a54a;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font:15px/1.55 var(--body)}
main{max-width:880px;margin:0 auto;padding-block:32px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1{font:600 clamp(28px,5vw,40px)/1.1 var(--display);margin:0 0 8px;text-wrap:balance}
.sub{color:var(--mute);margin:0 0 28px;max-width:62ch}
.funnel{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:36px}
.step{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:0;flex:1 1 140px}
.step b{display:block;font:600 24px var(--mono);font-variant-numeric:tabular-nums}
.step span{color:var(--mute);font-size:12.5px}
.step.hi{border-color:var(--accent)} .step.hi b{color:var(--accent)}
h2{font:600 22px/1.2 var(--display);margin:40px 0 6px;display:flex;gap:10px;align-items:baseline}
h2 .n{font:500 13px var(--mono);color:var(--mute)}
.lede{color:var(--mute);margin:0 0 14px;max-width:65ch}
.list{list-style:none;padding:0;margin:0;display:grid;gap:8px}
.row{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:grid;
grid-template-columns:minmax(150px,1.6fr) repeat(5,minmax(0,1fr));gap:6px 14px;align-items:start}
.id{min-width:0}.id b{font:600 16px var(--mono);display:block}.id span{color:var(--mute);font-size:13px}
.m{display:flex;flex-direction:column;min-width:0}.lab{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute)}
.val{font:500 14px var(--mono);font-variant-numeric:tabular-nums}.good{color:var(--good)}
.tags{grid-column:1/-1;display:flex;gap:6px;flex-wrap:wrap}.tags:empty{display:none}
.tag{font-size:11.5px;padding:1px 8px;border-radius:99px;border:1px solid currentColor}.ins{color:var(--accent)}.flag{color:var(--warn)}
.note{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);color:var(--mute);font-size:13.5px;max-width:68ch}
.note h3{font:600 15px var(--body);color:var(--fg);margin:16px 0 4px}
.wl{line-height:1.9}
.picks{margin:8px 0 36px}.picks h2{margin-top:0}.pick{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:14px 16px;margin:0 0 10px}
.pick h3{font:600 17px var(--display);margin:0 0 4px}.pick p{margin:6px 0 0;max-width:68ch}.pick .why{color:var(--mute);font-size:13.5px}
@media (max-width:640px){.row{grid-template-columns:repeat(2,minmax(0,1fr))}.id{grid-column:1/-1}}
"""

GROUPS = {
    "comp": ("Quality compounders on sale", "Grew revenue per share 10%+ a year with high quality scores, now priced as if "
             "growth stops. Closest to what Buffett actually buys. Main question for each: is the slowdown temporary?"),
    "value": ("Steady, slower businesses", "Cheap on current owner earnings but growing slowly. Good if the business holds "
              "up; check the 10-K for why the market doubts it (competition, a shrinking core product, debt)."),
    "cyc": ("Cyclicals at a discount", "Homebuilders, RVs, paper and similar. Already valued on 10-year average earnings, "
            "but a downturn can push earnings well below that. Buffett mostly avoids these; size small if at all."),
}


def _grp(r):
    if is_cyclical_sic(r.sic):
        return "cyc"
    if r.hist_growth >= 0.10 and r.quality >= 84:
        return "comp"
    return "value"


def _cap(x):
    return f"${x/1e9:.1f}B" if x >= 1e9 else f"${x/1e6:.0f}M"


def _p(x):
    return "–" if pd.isna(x) else f"{x*100:.0f}%"


def _row(r, new):
    tags = ""
    if str(r.insider_signal) == "True":
        tags += '<span class="tag ins">Insider buying</span>'
    if r.red_flags:
        tags += f'<span class="tag flag">{int(r.red_flags)} flag</span>'
    if new:
        tags += '<span class="tag ins">New this week</span>'
    name = r["name"].title() if r["name"].isupper() else r["name"]
    return (f'<li class="row"><div class="id"><b>{r.ticker}</b><span>{html.escape(name)}</span></div>'
            f'<div class="m"><span class="lab">Price / value</span><span class="val">${r.price:,.0f} / ${r.iv_per_share:,.0f}</span></div>'
            f'<div class="m"><span class="lab">Discount</span><span class="val good">{_p(r.margin_of_safety)}</span></div>'
            f'<div class="m"><span class="lab">Quality</span><span class="val">{r.quality:.0f}</span></div>'
            f'<div class="m"><span class="lab">Past growth</span><span class="val">{_p(r.hist_growth)}/yr</span></div>'
            f'<div class="m"><span class="lab">Size</span><span class="val">{r["size"]} · {_cap(r.market_cap)}</span></div>'
            f'<div class="tags">{tags}</div></li>')


def build(csv_path: str, listed: int | None = None, prev_csv: str | None = None,
          picks_html: str = "") -> tuple[str, dict]:
    t = pd.read_csv(csv_path)
    b = t[t.verdict == "BUY ZONE"].copy()
    w = t[t.verdict == "WATCHLIST"].copy()
    prev_buy = set()
    if prev_csv:
        p = pd.read_csv(prev_csv)
        prev_buy = set(p[p.verdict == "BUY ZONE"].ticker)
    b["g"] = b.apply(_grp, axis=1) if len(b) else []
    parts = []
    for k in ("comp", "value", "cyc"):
        s = b[b.g == k].sort_values("margin_of_safety", ascending=False)
        if len(s):
            parts.append(f'<section><h2>{GROUPS[k][0]} <span class="n">{len(s)}</span></h2>'
                         f'<p class="lede">{GROUPS[k][1]}</p><ul class="list">'
                         + "".join(_row(r, bool(prev_buy) and r.ticker not in prev_buy) for _, r in s.iterrows())
                         + "</ul></section>")
    wl = ", ".join(f"<b>{r.ticker}</b> (buy below ${r.buy_below:,.0f})"
                   for _, r in w.sort_values("margin_of_safety", ascending=False).head(20).iterrows())
    dropped = sorted(prev_buy - set(b.ticker)) if prev_buy else []
    vc = t.verdict.value_counts().to_dict()
    stats = {"listed": listed or 0, "passed": len(t), "buy": len(b), "watch": len(w),
             "exp": vc.get("WATCHLIST - EXPENSIVE", 0),
             "new": sorted(set(b.ticker) - prev_buy) if prev_buy else [], "dropped": dropped}
    when = datetime.now().strftime("%b %d, %Y").replace(" 0", " ")
    change = ""
    if prev_csv:
        change = (f'<p class="sub"><b>Since last scan:</b> new in buy zone: {", ".join(stats["new"]) or "none"}. '
                  f'Left buy zone: {", ".join(dropped) or "none"}.</p>')
    page = f"""<title>MoatScan Market Scan</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@500;600&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<main>
<h1>Buffett-style scan of the US market</h1>
<p class="sub">Every NYSE/Nasdaq company that files with the SEC, judged on 10 years of its own filings, then priced against today's quote. Run {when}. Updated weekly. Estimates, not advice.</p>
{change}
{('<section class="picks"><h2>This week\'s top picks</h2>' + picks_html + '</section>') if picks_html else ''}
<div class="funnel">
<div class="step"><b>{stats["listed"]:,}</b><span>listed companies read</span></div>
<div class="step"><b>{stats["passed"]}</b><span>pass quality + honest-books bar</span></div>
<div class="step"><b>{stats["exp"]}</b><span>good, but priced above value</span></div>
<div class="step"><b>{stats["watch"]}</b><span>good, small discount</span></div>
<div class="step hi"><b>{stats["buy"]}</b><span>30%+ below estimated value</span></div>
</div>
{"".join(parts)}
<div class="note">
<h3>Watchlist: good businesses, wait for these prices</h3>
<p class="wl">{wl}</p>
<h3>How the numbers work</h3>
<p><b>Value</b> is a 10-year discounted cash flow on owner earnings (operating cash minus maintenance capex and stock comp), at a 10% discount rate, assuming 75% of past per-share growth continues, capped at 12%. <b>Discount</b> is how far the price sits below that value; Buffett-style buying wants 30% or more. <b>Quality</b> scores 10 checks: returns on capital, margins, debt, cash conversion, dilution, and whether the business is shrinking.</p>
<h3>Left out on purpose</h3>
<p>Banks, insurers, REITs and other financials (the model doesn't fit them); foreign companies filing 20-F/40-F (share and currency math breaks); companies under $30M market cap; and businesses with revenue falling more than 5% a year.</p>
<h3>Before buying any of these</h3>
<p>Read the latest 10-K's business and risk sections and ask whether the advantage will still exist in 10 years. A big discount often means the market sees a problem the past numbers don't show yet.</p>
</div>
</main>"""
    return page, stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    prev = sys.argv[sys.argv.index("--prev") + 1] if "--prev" in sys.argv else None
    if prev in args:
        args.remove(prev)
    csv_path = args[0] if args else sorted(glob.glob("reports/moatscan_scan_*.csv"))[-1]
    listed = int(sys.argv[sys.argv.index("--listed") + 1]) if "--listed" in sys.argv else None
    picks = ""
    if "--picks" in sys.argv:
        pf = sys.argv[sys.argv.index("--picks") + 1]
        if pf in args:
            args.remove(pf)
        picks = open(pf).read()
    out = args[1] if len(args) > 1 else "reports/market-scan.html"
    page, stats = build(csv_path, listed, prev, picks)
    open(out, "w").write(page)
    print(stats)
