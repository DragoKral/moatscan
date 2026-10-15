"""Self-contained HTML report."""
from __future__ import annotations

import html
import math
from datetime import datetime

from .analysis import Result

ORDER = {"BUY ZONE": 0, "WATCHLIST": 1, "QUALITY - NEED PRICE": 2, "WATCHLIST - EXPENSIVE": 3,
         "AVOID": 4, "OUTSIDE CIRCLE": 5, "ERROR": 6}

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mute:#6b6a65;--line:#e4e1d9;--card:#fff;--good:#1f7a4d;--warn:#a36a00;--bad:#b3261e;--acc:#2b4a7a}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--mute:#9b9a94;--line:#2c2c29;--card:#1c1c1a;--good:#5cc28d;--warn:#e0a83a;--bad:#ef6b62;--acc:#8fb0e6}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Inter,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 60px}h1{font:600 28px Georgia,serif;margin:0 0 4px}
.sub{color:var(--mute);margin-bottom:24px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:16px 0}
.row{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.tk{font:700 20px Georgia,serif}.nm{color:var(--mute)}
.v{font-weight:700;padding:2px 10px;border-radius:99px;font-size:12px;letter-spacing:.04em;border:1px solid currentColor}
.v0{color:var(--good)}.v1,.v2,.v3{color:var(--warn)}.v4,.v5,.v6{color:var(--bad)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.k{border:1px solid var(--line);border-radius:8px;padding:8px 10px}.k b{display:block;font-size:18px}.k span{color:var(--mute);font-size:12px}
ul.c{list-style:none;padding:0;margin:8px 0}ul.c li{padding:4px 0;border-bottom:1px dashed var(--line)}
.p{color:var(--good)}.f{color:var(--bad)}.n{color:var(--mute)}.why{color:var(--mute);font-size:13px}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:16px 0 4px}
.hist{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;width:100%}td,th{padding:4px 8px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}th:first-child,td:first-child{text-align:left}
.rank a{color:var(--acc);text-decoration:none}.rank li{margin:3px 0}details summary{cursor:pointer;color:var(--acc)}
.note{font-size:13px;color:var(--mute)}
"""


def _p(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x*100:.{d}f}%"


def _m(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    a = abs(x)
    return f"{x/1e12:.2f}T" if a >= 1e12 else f"{x/1e9:.2f}B" if a >= 1e9 else f"{x/1e6:.0f}M"


def _d(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:,.2f}"


def _x(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1f}x"


def _check_li(c, flag=False):
    if c.passed is None:
        cls, mark = "n", "–"
    elif flag:
        cls, mark = ("f", "⚑") if c.passed else ("p", "✓")
    else:
        cls, mark = ("p", "✓") if c.passed else ("f", "✗")
    return (f'<li><span class="{cls}">{mark} <b>{html.escape(c.name)}</b></span> — {html.escape(c.value)}'
            f'<div class="why">{html.escape(c.why)}</div></li>')


def _card(r: Result) -> str:
    vc = f"v{ORDER.get(r.verdict, 6)}"
    out = [f'<section class="card" id="{r.ticker}"><div class="row"><span class="tk">{r.ticker}</span>'
           f'<span class="nm">{html.escape(r.name)}</span><span class="v {vc}">{r.verdict}</span>'
           + (f'<span class="nm">{r.size}-cap</span>' if getattr(r, "size", "") else "") + '</div>'
           f'<p>{html.escape(r.verdict_why)}</p>']
    v = r.valuation
    if v:
        k = [("Price", _d(v.get("price"))), ("Intrinsic value / sh", _d(v.get("iv_per_share"))),
             ("Buy below", _d(v.get("buy_below"))), ("Value if no growth", _d(v.get("iv_no_growth"))), ("Margin of safety", _p(v.get("margin_of_safety"), 0)),
             ("Owner-earnings yield", _p(v.get("oe_yield"))), ("10y Treasury", _p(v.get("treasury"), 2)),
             ("Growth price implies", _p(v.get("implied_growth"))), ("Growth assumed", _p(v.get("growth_used"))),
             ("Quality score", f"{r.quality_score:.0f}/100" if not math.isnan(r.quality_score) else "n/a"),
             ("Piotroski F", f"{r.piotroski}/9" if r.piotroski is not None else "n/a"),
             ("EV / EBIT", _x(v.get("ev_ebit"))), ("Price / FCF", _x(v.get("p_fcf"))),
             ("Market cap", _m(v.get("market_cap")))]
        out.append('<div class="kpis">' + "".join(f'<div class="k"><b>{b}</b><span>{a}</span></div>' for a, b in k) + "</div>")
        out.append(f'<p class="note">Normalized owner earnings {_m(v.get("base_owner_earnings"))}/yr · net cash '
                   f'{_m(v.get("net_cash"))} · discount rate {_p(v.get("discount_rate"))} · '
                   f'past revenue/share growth {_p(v.get("hist_growth"))}</p>')
    if r.quality:
        out.append("<h3>Business quality</h3><ul class='c'>" + "".join(_check_li(c) for c in r.quality) + "</ul>")
    if r.flags:
        out.append("<h3>Accounting red flags</h3><ul class='c'>" + "".join(_check_li(c, True) for c in r.flags) + "</ul>")
    ins = r.insiders
    if ins:
        msg = (f"{len(ins['buyers'])} insiders bought ${ins['buy_usd']:,.0f} on the open market in the last "
               f"{ins['window_days']} days (sold ${ins['sell_usd']:,.0f})." if ins.get("form4_checked") is not None else "")
        if ins.get("cluster"):
            msg = "⚑ Insider buying signal: " + msg
        out.append(f"<h3>Insiders (Form 4)</h3><p>{html.escape(msg)}</p>")
        if ins.get("buys"):
            out.append("<ul class='c'>" + "".join(
                f"<li>{b['date']} · {html.escape(b['owner'])} {('(' + html.escape(b['title']) + ')') if b['title'] else ''}"
                f" · {b['shares']:,.0f} sh @ {_d(b['price'])}</li>" for b in ins["buys"]) + "</ul>")
    for n in r.notes:
        out.append(f'<p class="note">{html.escape(n)}</p>')
    t = r.table
    if len(t):
        rows = [("Revenue", "revenue", _m), ("Gross margin", "gross_margin", _p), ("Op margin", "op_margin", _p),
                ("ROIC", "roic", _p), ("ROE", "roe", _p), ("Net income", "net_income", _m),
                ("Operating cash", "cfo", _m), ("Capex", "capex", _m), ("Owner earnings", "owner_earnings", _m),
                ("Total debt", "total_debt", _m), ("Cash + ST inv", "cash_all", _m),
                ("Diluted shares", "shares_diluted", _m)]
        hdr = "".join(f"<th>{y}</th>" for y in t.index)
        body = "".join(f"<tr><td>{lab}</td>" + "".join(f"<td>{f(t.loc[y, c]) if c in t else 'n/a'}</td>" for y in t.index)
                       + "</tr>" for lab, c, f in rows)
        out.append(f"<details><summary>10-year history from SEC filings</summary><div class='hist'><table>"
                   f"<tr><th>FY</th>{hdr}</tr>{body}</table></div></details>")
    out.append("</section>")
    return "".join(out)


def render(results: list[Result], title: str = "MoatScan") -> str:
    results = sorted(results, key=lambda r: (ORDER.get(r.verdict, 6),
                                             -(r.valuation.get("margin_of_safety") or -9) if r.valuation else 9,
                                             -(r.quality_score if not math.isnan(r.quality_score) else 0)))
    rank = "".join(
        f'<li><a href="#{r.ticker}"><b>{r.ticker}</b></a> — <span class="v{ORDER.get(r.verdict, 6)}">{r.verdict}</span>'
        f'{" · MoS " + _p(r.valuation.get("margin_of_safety"), 0) if r.valuation.get("margin_of_safety") is not None else ""}'
        f'{" · quality " + format(r.quality_score, ".0f") if not math.isnan(r.quality_score) else ""}'
        f'{" · ⚑ insider buying" if r.insiders.get("cluster") else ""}</li>' for r in results)
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style></head><body><main><h1>{title}</h1>"
            f"<div class='sub'>Buffett-style read of SEC filings · {datetime.now():%b %d, %Y %H:%M} · "
            f"estimates, not advice</div><div class='card rank'><h3>Ranking</h3><ul class='c'>{rank}</ul></div>"
            + "".join(_card(r) for r in results) + "</main></body></html>")
