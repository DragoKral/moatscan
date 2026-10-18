"""Buffett-style analysis from annual SEC data.

1. Business quality: the checks Buffett describes in his letters (consistent earnings,
   high returns on capital without leverage, durable margins, little debt, cash-like
   earnings, shareholder-friendly capital allocation).
2. Accounting honesty: accrual, receivables/inventory, Piotroski F-score, Beneish M-score.
3. Price vs. production: owner earnings (1986 letter), a conservative owner-earnings DCF,
   a reverse DCF (what growth the price already assumes), and a margin-of-safety verdict.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

nan = float("nan")


def _safe_div(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = a / b
    if isinstance(r, pd.Series):
        return r.replace([np.inf, -np.inf], np.nan)
    return r if (r is not None and np.isfinite(r)) else nan


def _avg(s: pd.Series) -> pd.Series:
    return (s + s.shift(1)) / 2


def _cagr(first, last, years):
    if years <= 0 or not (first and last) or first <= 0 or last <= 0 or math.isnan(first) or math.isnan(last):
        return nan
    return (last / first) ** (1 / years) - 1


# ---------------------------------------------------------------- per-year metrics
def derive(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    rev = d["revenue"]
    d["gross_margin"] = _safe_div(d["gross_profit"], rev)
    d["op_margin"] = _safe_div(d["op_income"], rev)
    d["net_margin"] = _safe_div(d["net_income"], rev)
    tax_rate = _safe_div(d["tax"], d["pretax"]).clip(0, 0.35).fillna(0.21)
    d["nopat"] = d["op_income"] * (1 - tax_rate)
    ic = d["equity"] + d["total_debt"] - d["cash_all"]
    ic = ic.where(ic > 0)
    d["invested_capital"] = ic
    d["roic"] = _safe_div(d["nopat"], _avg(ic).fillna(ic))
    eq = d["equity"].where(d["equity"] > 0)
    d["roe"] = _safe_div(d["net_income"], _avg(eq).fillna(eq))
    d["fcf"] = d["cfo"] - d["capex"]
    # Maintenance capex (Greenwald): capex minus the capex needed to support revenue growth.
    ppe_to_sales = _safe_div(d["ppe"], rev).rolling(3, min_periods=1).mean()
    growth_capex = (ppe_to_sales * rev.diff()).clip(lower=0)
    maint = (d["capex"] - growth_capex).clip(lower=0)
    maint = maint.fillna(np.minimum(d["capex"], d["da"]))
    maint = np.maximum(maint, 0.5 * d["capex"].fillna(0))  # never assume >50% of capex is growth
    d["maint_capex"] = maint
    d["owner_earnings"] = d["cfo"] - d["maint_capex"] - d["sbc"].fillna(0)
    # partnerships / Up-C / majority-owned subs: cash flow is consolidated, but only part belongs
    # to these shareholders. Scale by parent share of profit.
    own = (d["net_income"] / d["profit_total"]).where((d["profit_total"] > 0) & (d["net_income"] > 0))
    d["own_frac"] = own.clip(0.05, 1).fillna(1.0)
    d["owner_earnings"] = d["owner_earnings"] * d["own_frac"]
    # earnings-based version (Buffett's 1986 definition: net income + D&A - maintenance capex);
    # steadier than the cash version, which swings with inventory and receivables
    d["owner_earnings_ni"] = (d["net_income"] + d["da"].fillna(0) - d["maint_capex"]) * d["own_frac"]
    sh = d["shares_diluted"]
    d["oe_ps"] = _safe_div(d["owner_earnings"], sh)
    d["rev_ps"] = _safe_div(rev, sh)
    d["bvps"] = _safe_div(d["equity"], sh)
    d["dps"] = _safe_div(d["dividends"].fillna(0), sh)
    d["debt_to_oe"] = _safe_div(d["total_debt"], d["owner_earnings"].where(d["owner_earnings"] > 0))
    d["int_cover"] = _safe_div(d["op_income"], d["interest"].where(d["interest"] > 0))
    d["cfo_to_ni"] = _safe_div(d["cfo"], d["net_income"].where(d["net_income"] > 0))
    d["capex_to_cfo"] = _safe_div(d["capex"], d["cfo"].where(d["cfo"] > 0))
    d["accruals"] = _safe_div(d["net_income"] - d["cfo"], _avg(d["assets"]))
    d["sbc_to_cfo"] = _safe_div(d["sbc"], d["cfo"].where(d["cfo"] > 0))
    return d


# ---------------------------------------------------------------- result containers
@dataclass
class Check:
    name: str
    passed: bool | None  # None = not enough data
    value: str
    why: str
    weight: float = 1.0

    def __post_init__(self):
        if self.passed is not None:
            self.passed = bool(self.passed)  # numpy bools -> plain bools


@dataclass
class Result:
    ticker: str
    name: str
    years: list[int]
    table: pd.DataFrame
    quality: list[Check] = field(default_factory=list)
    flags: list[Check] = field(default_factory=list)
    quality_score: float = nan
    piotroski: int | None = None
    beneish: float | None = None
    valuation: dict = field(default_factory=dict)
    insiders: dict = field(default_factory=dict)
    verdict: str = ""
    verdict_why: str = ""
    notes: list[str] = field(default_factory=list)
    radar: dict = field(default_factory=dict)


def _fmt_pct(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x*100:.1f}%"


def _fmt_x(x):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1f}x"


# ---------------------------------------------------------------- quality checklist
def quality_checks(d: pd.DataFrame, cfg: dict) -> list[Check]:
    q = cfg["quality"]
    c: list[Check] = []
    last10 = d.tail(10)
    n = last10["net_income"].notna().sum()

    pos = int((last10["net_income"] > 0).sum())
    c.append(Check("Consistent earnings", None if n < 5 else pos >= max(n - 1, 0) and n >= 5,
                   f"profitable {pos} of {n} yrs",
                   "Buffett wants a long, unbroken record of earning power, not a turnaround.", 1.5))

    roic_med = last10["roic"].median()
    c.append(Check("High return on capital", None if math.isnan(roic_med) else roic_med >= q["min_roic"],
                   f"median ROIC {_fmt_pct(roic_med)}",
                   f"A moat shows up as returns on invested capital well above cost of capital (>= {q['min_roic']:.0%}).",
                   2.0))

    roe_med = last10["roe"].median()
    lev = (last10["liabilities"] / last10["equity"].where(last10["equity"] > 0)).median()
    c.append(Check("High ROE without leverage",
                   None if math.isnan(roe_med) else roe_med >= q["min_roe"] and (math.isnan(lev) or lev <= q["max_liab_to_equity"]),
                   f"median ROE {_fmt_pct(roe_med)}, liabilities/equity {_fmt_x(lev)}",
                   "Good ROE that isn't manufactured with debt.", 1.0))

    gm = last10["gross_margin"]
    om = last10["op_margin"]
    om_cv = om.std() / abs(om.mean()) if om.notna().sum() >= 5 and om.mean() else nan
    steady = not math.isnan(om_cv) and om_cv <= q["max_margin_cv"]
    high_gm = gm.notna().any() and gm.median() >= q["min_gross_margin"]
    # high gross margin only counts if margins don't swing like a commodity (cyclicals fail)
    durable = steady or (high_gm and not math.isnan(om_cv) and om_cv <= 2 * q["max_margin_cv"])
    c.append(Check("Durable margins (pricing power)", None if om.notna().sum() < 5 else bool(durable),
                   f"median gross margin {_fmt_pct(gm.median())}, op-margin variability {_fmt_x(om_cv) if not math.isnan(om_cv) else 'n/a'}",
                   "High or steady margins mean customers can't easily go elsewhere.", 1.5))

    last = d.iloc[-1]
    oe_norm = d["owner_earnings"].tail(5).median()
    debt_oe = last["total_debt"] / oe_norm if oe_norm and oe_norm > 0 else nan
    debt_ok = (last["total_debt"] <= 0) or (not math.isnan(debt_oe) and debt_oe <= q["max_debt_to_oe"])
    c.append(Check("Low debt", bool(debt_ok),
                   "no debt" if last["total_debt"] <= 0 else f"debt = {_fmt_x(debt_oe)} owner earnings",
                   f"Debt payable from <= {q['max_debt_to_oe']:.0f} years of normalized owner earnings.", 1.5))

    conv = last10["cfo"].sum() / last10["net_income"].sum() if last10["net_income"].sum() > 0 else nan
    c.append(Check("Earnings are real cash", None if math.isnan(conv) else conv >= q["min_cash_conversion"],
                   f"10y operating cash / net income {_fmt_x(conv)}",
                   "Reported profit should turn into cash.", 1.0))

    capint = last10["capex"].sum() / last10["cfo"].sum() if last10["cfo"].sum() > 0 else nan
    c.append(Check("Capital-light", None if math.isnan(capint) else capint <= q["max_capex_to_cfo"],
                   f"capex consumes {_fmt_pct(capint)} of operating cash",
                   "Buffett prefers businesses that don't have to pour profits back into plants to stand still.", 1.0))

    sh = d["shares_diluted"].dropna()
    sh_cagr = _cagr(sh.iloc[0], sh.iloc[-1], len(sh) - 1) if len(sh) >= 3 else nan
    c.append(Check("Not diluting owners", None if math.isnan(sh_cagr) else sh_cagr <= q["max_share_growth"],
                   f"share count {('+' if sh_cagr > 0 else '')}{_fmt_pct(sh_cagr)}/yr" if not math.isnan(sh_cagr) else "n/a",
                   "Share count flat or shrinking = management treats you as a partner.", 1.0))

    # value compounding: book value per share growth plus dividends paid (per-share)
    bv = d["bvps"].dropna()
    comp = nan
    if len(bv) >= 5 and bv.iloc[0] > 0 and bv.iloc[-1] > 0:
        divs = d.loc[bv.index, "dps"].iloc[1:].sum()
        comp = _cagr(bv.iloc[0], bv.iloc[-1] + divs, len(bv) - 1)
    rev_ps = d["rev_ps"].dropna()
    rps_cagr = _cagr(rev_ps.iloc[0], rev_ps.iloc[-1], len(rev_ps) - 1) if len(rev_ps) >= 5 else nan
    passed = None
    if not (math.isnan(comp) and math.isnan(rps_cagr)):
        passed = (not math.isnan(comp) and comp >= q["min_value_compounding"]) or \
                 (not math.isnan(rps_cagr) and rps_cagr >= q["min_value_compounding"])
    c.append(Check("Compounds per-share value", passed,
                   f"book value + dividends {_fmt_pct(comp)}/yr, revenue/share {_fmt_pct(rps_cagr)}/yr",
                   "Retained earnings should be turned into more value per share over time.", 1.0))
    rv = d["revenue"].dropna()
    r3 = _cagr(rv.iloc[-4], rv.iloc[-1], 3) if len(rv) >= 4 else nan
    c.append(Check("Not a shrinking business", None if math.isnan(r3) else r3 >= -0.05,
                   f"revenue {'+' if r3 >= 0 else ''}{_fmt_pct(r3)}/yr over 3y" if not math.isnan(r3) else "n/a",
                   "A cheap price on a melting business is a value trap, not a margin of safety.", 1.5))
    return c


# ---------------------------------------------------------------- accounting red flags
def piotroski(d: pd.DataFrame) -> int | None:
    if len(d) < 2:
        return None
    t, p = d.iloc[-1], d.iloc[-2]
    roa_t = _safe_div(t["net_income"], t["assets"])
    roa_p = _safe_div(p["net_income"], p["assets"])
    tests = [
        t["net_income"] > 0,
        t["cfo"] > 0,
        roa_t > roa_p,
        t["cfo"] > t["net_income"],
        _safe_div(t["lt_debt"], t["assets"]) <= _safe_div(p["lt_debt"], p["assets"]) if not math.isnan(t["lt_debt"]) else t["total_debt"] <= p["total_debt"],
        _safe_div(t["cur_assets"], t["cur_liab"]) > _safe_div(p["cur_assets"], p["cur_liab"]),
        t["shares_diluted"] <= p["shares_diluted"] * 1.005,
        t["gross_margin"] > p["gross_margin"],
        _safe_div(t["revenue"], t["assets"]) > _safe_div(p["revenue"], p["assets"]),
    ]
    return int(sum(bool(x) for x in tests))


def beneish(d: pd.DataFrame) -> float | None:
    if len(d) < 2:
        return None
    t, p = d.iloc[-1], d.iloc[-2]
    missing = 0

    def idx(x):
        nonlocal missing
        if x is None or (isinstance(x, float) and math.isnan(x)):
            missing += 1
            return 1.0
        return float(x)

    dsri = idx(_safe_div(_safe_div(t["receivables"], t["revenue"]), _safe_div(p["receivables"], p["revenue"])))
    gmi = idx(_safe_div(p["gross_margin"], t["gross_margin"]))
    aq = lambda r: 1 - _safe_div(r["cur_assets"] + r["ppe"], r["assets"])  # noqa: E731
    aqi = idx(_safe_div(aq(t), aq(p)))
    sgi = idx(_safe_div(t["revenue"], p["revenue"]))
    dep = lambda r: _safe_div(r["da"], r["da"] + r["ppe"])  # noqa: E731
    depi = idx(_safe_div(dep(p), dep(t)))
    sgai = idx(_safe_div(_safe_div(t["sga"], t["revenue"]), _safe_div(p["sga"], p["revenue"])))
    tata = _safe_div(t["net_income"] - t["cfo"], t["assets"])
    if math.isnan(tata):
        missing += 1
        tata = 0.0
    lv = lambda r: _safe_div(r["cur_liab"] + (0 if math.isnan(r["lt_debt"]) else r["lt_debt"]), r["assets"])  # noqa: E731
    lvgi = idx(_safe_div(lv(t), lv(p)))
    if missing > 3:
        return None
    return (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
            - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)


def red_flags(d: pd.DataFrame, cfg: dict) -> list[Check]:
    f = cfg["red_flags"]
    out: list[Check] = []
    last3 = d.tail(3)
    acc = last3["accruals"].mean()
    out.append(Check("High accruals", None if math.isnan(acc) else acc > f["max_accruals"],
                     f"(net income - op cash)/assets {_fmt_pct(acc)} (3y avg)",
                     "Profits running ahead of cash is the most common sign of aggressive accounting (Sloan 1996)."))

    def growth_gap(col):
        s, r = d[col].dropna(), d["revenue"]
        if len(s) < 4:
            return nan
        g1 = _cagr(s.iloc[-4], s.iloc[-1], 3)
        g2 = _cagr(r.loc[s.index[-4]], r.loc[s.index[-1]], 3)
        return g1 - g2 if not (math.isnan(g1) or math.isnan(g2)) else nan

    rg = growth_gap("receivables")
    out.append(Check("Receivables outrunning sales", None if math.isnan(rg) else rg > f["max_growth_gap"],
                     f"receivables grew {_fmt_pct(rg)}/yr faster than revenue (3y)",
                     "Can mean channel stuffing or loosening credit to book sales."))
    ig = growth_gap("inventory")
    out.append(Check("Inventory piling up", None if math.isnan(ig) else ig > f["max_growth_gap"],
                     f"inventory grew {_fmt_pct(ig)}/yr faster than revenue (3y)",
                     "Unsold goods building up often precede write-downs."))
    sbc = last3["sbc_to_cfo"].mean()
    out.append(Check("Heavy stock compensation", None if math.isnan(sbc) else sbc > f["max_sbc_to_cfo"],
                     f"stock comp = {_fmt_pct(sbc)} of operating cash",
                     "Stock comp is a real cost to owners even though it's non-cash."))
    last = d.iloc[-1]
    gw = _safe_div((0 if math.isnan(last["goodwill"]) else last["goodwill"]) +
                   (0 if math.isnan(last["intangibles"]) else last["intangibles"]), last["assets"])
    out.append(Check("Acquisition-heavy balance sheet", None if math.isnan(gw) else gw > f["max_goodwill_share"],
                     f"goodwill + intangibles = {_fmt_pct(gw)} of assets",
                     "Growth bought through acquisitions is harder to judge and can be written down."))
    m = beneish(d)
    out.append(Check("Beneish manipulation score", None if m is None else m > f["beneish_threshold"],
                     "n/a" if m is None else f"M = {m:.2f} (flag above {f['beneish_threshold']})",
                     "Statistical model of earnings manipulation from 8 ratios."))
    eq_neg = last["equity"] <= 0
    out.append(Check("Negative equity", bool(eq_neg), f"equity {last['equity']/1e9:,.2f}B" if not math.isnan(last["equity"]) else "n/a",
                     "Can be benign (heavy buybacks) but makes ROE meaningless and raises leverage risk."))
    return out


# ---------------------------------------------------------------- valuation
def dcf_value(oe0: float, g: float, years: int, g_term: float, r: float) -> float:
    pv, cf = 0.0, oe0
    for t in range(1, years + 1):
        cf *= 1 + g
        pv += cf / (1 + r) ** t
    term = cf * (1 + g_term) / (r - g_term)
    return pv + term / (1 + r) ** years


def implied_growth(target: float, oe0: float, years: int, g_term: float, r: float) -> float:
    if oe0 <= 0 or target <= 0:
        return nan
    lo, hi = -0.30, 0.60
    if dcf_value(oe0, hi, years, g_term, r) < target:
        return hi
    if dcf_value(oe0, lo, years, g_term, r) > target:
        return lo
    for _ in range(80):
        mid = (lo + hi) / 2
        if dcf_value(oe0, mid, years, g_term, r) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


CYCLICAL_SIC = [(1000, 1499), (1520, 1531), (2600, 2631), (2800, 2829), (2860, 2869), (2900, 2999),
                (3310, 3399), (3530, 3537), (3711, 3716), (3674, 3674), (4400, 4499), (4011, 4213), (3720, 3728),
                (2400, 2499), (3250, 3299), (5500, 5599)]


def is_cyclical_sic(sic) -> bool:
    return bool(sic) and any(a <= int(sic) <= b for a, b in CYCLICAL_SIC)


def valuation(d: pd.DataFrame, shares_out: float | None, price: float | None, cfg: dict, treasury: float,
              sic=None, fwd_growth=None) -> dict:
    v = cfg["valuation"]
    oe = d["owner_earnings"].dropna()
    om = d["op_margin"].tail(10).dropna()
    cyclical = is_cyclical_sic(sic) or bool(len(om) >= 5 and ((d["net_income"].tail(10) <= 0).any() or (om.std() / abs(om.mean()) > 0.5 if om.mean() else True)))
    # median of 5y resists one-off cash hits (tax deposits, legal settlements); cyclicals use full-cycle mean
    base = oe.tail(10).mean() if cyclical else oe.tail(5).median()
    oe_ni = d["owner_earnings_ni"].dropna()
    if len(oe_ni) >= 3:  # use the lower of cash-based and earnings-based owner earnings (one-off cash swings)
        base_ni = oe_ni.tail(10).mean() if cyclical else min(oe_ni.tail(5).median(), oe_ni.tail(2).mean())
        base = min(base, base_ni)
    rv = d["revenue"].dropna()
    shrinking = len(rv) >= 4 and _cagr(rv.iloc[-4], rv.iloc[-1], 3) < -0.05
    if shrinking or (not cyclical and oe.tail(2).mean() < base):
        base = min(base, oe.tail(2).mean())  # business getting worse: value on what it earns now
    rps = d["rev_ps"].dropna().tail(11)
    hist_g = _cagr(rps.iloc[0], rps.iloc[-1], len(rps) - 1) if len(rps) >= 5 else nan
    # recent growth (3y) caps the long-run rate: a business that stalled shouldn't get credit for its boom years
    rps3 = d["rev_ps"].dropna().tail(4)
    recent_g = _cagr(rps3.iloc[0], rps3.iloc[-1], len(rps3) - 1) if len(rps3) >= 4 else nan
    fg = nan if fwd_growth is None else float(fwd_growth)
    # analysts' forward forecast caps it too: past filings can't see a slowdown that started this year
    cands = [x for x in (hist_g * v["growth_haircut"] if not math.isnan(hist_g) else nan, recent_g, fg)
             if not math.isnan(x)]
    g = min(max(min(cands), 0.0), v["max_growth"]) if cands else 0.0
    r = max(v["discount_rate"], treasury + v["equity_premium"])
    dil = d["shares_diluted"].dropna()
    if len(dil) and (not shares_out or not 0.75 <= shares_out / dil.iloc[-1] <= 1.33):
        shares_out = dil.iloc[-1]  # cover-page count missing classes or stale
    last = d.iloc[-1]
    net_cash = last["cash_all"] - last["total_debt"]
    eq = last["equity"]
    if net_cash > 0 and not math.isnan(eq) and net_cash > max(eq, 0):
        # cash beyond shareholders' equity is funded by others (client float, deposits, payables)
        net_cash = max(eq, 0)
    shares_out = shares_out if shares_out else None
    out = {"cyclical": cyclical, "base_owner_earnings": base, "hist_growth": hist_g, "growth_used": g,
           "recent_growth": recent_g, "analyst_growth": fg, "discount_rate": r, "terminal_growth": v["terminal_growth"], "net_cash": net_cash,
           "shares_out": shares_out, "price": price, "treasury": treasury}
    if math.isnan(base) or base <= 0 or not shares_out:
        out["note"] = "No positive normalized owner earnings - can't value on earning power."
        return out
    ev = dcf_value(base, g, v["years"], v["terminal_growth"], r)
    equity = ev + net_cash
    iv = equity / shares_out
    out.update(intrinsic_value=equity, iv_per_share=iv, buy_below=iv * (1 - v["margin_of_safety"]))
    iv0 = (dcf_value(base, 0.0, v["years"], v["terminal_growth"], r) + net_cash) / shares_out
    out["iv_no_growth"] = iv0
    if price and not math.isnan(price) and price > 0:
        mcap = price * shares_out
        out["market_cap"] = mcap
        out["margin_of_safety"] = 1 - price / iv if iv > 0 else nan
        out["mos_no_growth"] = 1 - price / iv0 if iv0 > 0 else nan
        out["oe_yield"] = base / (mcap - net_cash) if mcap - net_cash > 0 else nan
        out["implied_growth"] = implied_growth(mcap - net_cash, base, v["years"], v["terminal_growth"], r)
        out["p_fcf"] = mcap / last["fcf"] if last["fcf"] > 0 else nan
        out["p_b"] = mcap / last["equity"] if last["equity"] > 0 else nan
        ebit = last["op_income"]
        out["ev_ebit"] = (mcap - net_cash) / ebit if ebit and ebit > 0 else nan
    return out


# ---------------------------------------------------------------- verdict
def score_and_verdict(res: Result, cfg: dict) -> None:
    graded = [c for c in res.quality if c.passed is not None]
    if graded:
        res.quality_score = 100 * sum(c.weight for c in graded if c.passed) / sum(c.weight for c in graded)
    nflags = sum(1 for f in res.flags if f.passed)
    v = res.valuation
    mos = v.get("margin_of_safety")
    good = res.quality_score >= cfg["verdict"]["min_quality"] and nflags <= cfg["verdict"]["max_flags"]
    if v.get("note"):
        res.verdict, res.verdict_why = "AVOID", v["note"]
    elif any(c.name == "Not a shrinking business" and c.passed is False for c in res.quality):
        res.verdict = "AVOID"
        res.verdict_why = "Revenue shrinking >5%/yr - cheap on past earnings, but likely a value trap."
    elif not good:
        res.verdict = "AVOID"
        res.verdict_why = (f"Business quality {res.quality_score:.0f}/100" +
                           (f", {nflags} accounting red flags" if nflags else "") +
                           " - doesn't meet the bar for a long-term hold regardless of price.")
    elif mos is None or (isinstance(mos, float) and math.isnan(mos)):
        res.verdict, res.verdict_why = "QUALITY - NEED PRICE", "Passes quality; add a price to judge value."
    elif mos >= cfg["valuation"]["margin_of_safety"]:
        res.verdict = "BUY ZONE"
        res.verdict_why = f"Good business at {mos:.0%} below estimated intrinsic value."
    elif mos >= 0:
        res.verdict = "WATCHLIST"
        res.verdict_why = f"Good business, but only {mos:.0%} margin of safety - wait for a better price."
    else:
        res.verdict = "WATCHLIST - EXPENSIVE"
        res.verdict_why = (f"Good business priced {-mos:.0%} above estimated value; price assumes "
                           f"{_fmt_pct(v.get('implied_growth'))}/yr owner-earnings growth for 10 years.")
    if v.get("cyclical"):
        res.notes.append("Cyclical business (by industry or earnings history): valued on 10-year average owner "
                         "earnings, not recent boom years.")


def analyze(ticker: str, name: str, df: pd.DataFrame, shares_out, price, cfg: dict, treasury: float,
            sic=None, fwd_growth=None) -> Result:
    d = derive(df)
    res = Result(ticker=ticker, name=name, years=list(d.index), table=d)
    if d["revenue"].notna().sum() < 3 or d["cfo"].notna().sum() < 3:
        res.verdict = "OUTSIDE CIRCLE"
        res.verdict_why = ("Financial statements don't fit this model (banks, insurers, REITs, or too little "
                           "history). Analyze by hand.")
        return res
    res.quality = quality_checks(d, cfg)
    res.flags = red_flags(d, cfg)
    res.piotroski = piotroski(d)
    res.beneish = beneish(d)
    res.valuation = valuation(d, shares_out, price, cfg, treasury, sic, fwd_growth)
    score_and_verdict(res, cfg)
    return res
