"""Turn SEC XBRL company facts into clean annual series (one value per fiscal year).

Companies switch XBRL tags over the years (e.g. Revenues -> RevenueFromContractWithCustomer...),
so each field lists fallback tags; years are filled from the first tag that has them.
Only 10-K (or 20-F/40-F) full-year values are used; restatements keep the latest filing.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

# field -> (kind, unit, [tags in priority order])
FIELDS: dict[str, tuple[str, str, list[str]]] = {
    "revenue": ("flow", "USD", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                                "SalesRevenueGoodsNet"]),
    "cogs": ("flow", "USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    "gross_profit": ("flow", "USD", ["GrossProfit"]),
    "sga": ("flow", "USD", ["SellingGeneralAndAdministrativeExpense"]),
    "op_income": ("flow", "USD", ["OperatingIncomeLoss"]),
    "pretax": ("flow", "USD", [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    "tax": ("flow", "USD", ["IncomeTaxExpenseBenefit"]),
    "interest": ("flow", "USD", ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]),
    "net_income": ("flow", "USD", ["NetIncomeLoss", "ProfitLoss"]),
    "profit_total": ("flow", "USD", ["ProfitLoss"]),  # incl. noncontrolling interests
    "cfo": ("flow", "USD", ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": ("flow", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    "da": ("flow", "USD", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                           "DepreciationAmortizationAndAccretionNet", "Depreciation"]),
    "sbc": ("flow", "USD", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]),
    "dividends": ("flow", "USD", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    "buybacks": ("flow", "USD", ["PaymentsForRepurchaseOfCommonStock"]),
    "shares_diluted": ("flow", "shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    "eps_diluted": ("flow", "USD/shares", ["EarningsPerShareDiluted"]),
    "assets": ("inst", "USD", ["Assets"]),
    "cur_assets": ("inst", "USD", ["AssetsCurrent"]),
    "cur_liab": ("inst", "USD", ["LiabilitiesCurrent"]),
    "liabilities": ("inst", "USD", ["Liabilities"]),
    "equity": ("inst", "USD", ["StockholdersEquity",
                               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    "cash": ("inst", "USD", ["CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    "st_invest": ("inst", "USD", ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                                  "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]),
    # debt: combined tags (incl. current portion) preferred; else noncurrent + current
    "debt_combined": ("inst", "USD", ["DebtAndCapitalLeaseObligations",
                                      "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                                      "LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]),
    "lt_debt": ("inst", "USD", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]),
    "debt_current": ("inst", "USD", ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                                     "DebtCurrent"]),
    "commercial_paper": ("inst", "USD", ["CommercialPaper"]),
    "st_borrowings": ("inst", "USD", ["ShortTermBorrowings", "OtherShortTermBorrowings"]),
    "credit_line": ("inst", "USD", ["LineOfCredit", "LongTermLineOfCredit", "LinesOfCreditCurrent",
                                    "LongTermLineOfCreditNoncurrent"]),
    "receivables": ("inst", "USD", ["AccountsReceivableNetCurrent"]),
    "inventory": ("inst", "USD", ["InventoryNet"]),
    "goodwill": ("inst", "USD", ["Goodwill"]),
    "intangibles": ("inst", "USD", ["IntangibleAssetsNetExcludingGoodwill"]),
    "ppe": ("inst", "USD", ["PropertyPlantAndEquipmentNet"]),
    "retained": ("inst", "USD", ["RetainedEarningsAccumulatedDeficit"]),
}

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _series(facts: dict, tag: str, unit: str, kind: str) -> dict[int, tuple[float, str]]:
    """Return {fiscal_year_end_year: (value, filed)} for one tag."""
    node = None
    for ns in ("us-gaap", "ifrs-full"):
        node = facts.get("facts", {}).get(ns, {}).get(tag)
        if node:
            break
    if not node:
        return {}
    rows = node.get("units", {}).get(unit) or []
    out: dict[str, tuple[float, str]] = {}  # keyed by period end date
    for r in rows:
        if r.get("form") not in ANNUAL_FORMS:
            continue
        if kind == "flow":
            if "start" not in r:
                continue
            days = (_d(r["end"]) - _d(r["start"])).days
            if not 340 <= days <= 380:
                continue  # skip quarters / odd stubs
        end = r["end"]
        prev = out.get(end)
        if prev is None or r.get("filed", "") > prev[1]:  # latest filing wins (restatements)
            out[end] = (float(r["val"]), r.get("filed", ""))
    # index by year of period end; if two ends land in one year keep the later end
    by_year: dict[int, tuple[float, str, str]] = {}
    for end, (v, filed) in out.items():
        y = _d(end).year
        if y not in by_year or end > by_year[y][2]:
            by_year[y] = (v, filed, end)
    return {y: (v, f) for y, (v, f, _) in by_year.items()}


SPLITS = [2, 3, 4, 5, 7, 8, 10, 15, 20, 1.5]


def split_adjust(sh: pd.Series) -> pd.Series:
    """Older 10-Ks report pre-split share counts. Detect year-over-year jumps that match a
    common split ratio (within 12%, to allow for that year's buybacks/issuance) and restate
    earlier years on the post-split basis."""
    sh = sh.copy()
    idx = [i for i in sh.index if pd.notna(sh[i])]
    for a, b in zip(reversed(idx[:-1]), reversed(idx[1:])):
        r = sh[b] / sh[a]
        for k in SPLITS:
            for f in (k, 1 / k):
                if abs(r / f - 1) <= 0.12:
                    sh.loc[sh.index <= a] *= f
                    break
            else:
                continue
            break
    return sh


def annual_table(facts: dict, years: int = 12) -> pd.DataFrame:
    """DataFrame indexed by fiscal year (int), one column per field. NaN where unreported."""
    cols = {}
    for field, (kind, unit, tags) in FIELDS.items():
        merged: dict[int, float] = {}
        for tag in tags:
            for y, (v, _) in _series(facts, tag, unit, kind).items():
                merged.setdefault(y, v)
        cols[field] = merged
    df = pd.DataFrame(cols).sort_index()
    if df.empty:
        return df
    df = df[df.index >= df.index.max() - years + 1]
    # derived gaps
    if "gross_profit" in df and "cogs" in df:
        df["gross_profit"] = df["gross_profit"].fillna(df["revenue"] - df["cogs"])
    parts = df[["lt_debt", "debt_current"]].sum(axis=1, min_count=1)
    st = df[["commercial_paper", "st_borrowings"]].fillna(0).sum(axis=1)
    # revolver borrowings are often tagged separately; add them when the debt tags clearly exclude them
    cl = df["credit_line"].fillna(0)
    base_debt = df["debt_combined"].fillna(df[["lt_debt", "debt_current"]].sum(axis=1, min_count=1)).fillna(0)
    st = st + cl.where(cl > base_debt * 0.05, 0).where(base_debt < cl * 2, 0)
    df["liabilities"] = df["liabilities"].fillna(df["assets"] - df["equity"])
    df["total_debt"] = df["debt_combined"].fillna(parts).fillna(0) + st
    df["lt_debt"] = df["lt_debt"].fillna(df["debt_combined"])
    df["shares_diluted"] = split_adjust(df["shares_diluted"])
    sh_ok = df["shares_diluted"].dropna()
    if len(sh_ok) and sh_ok.index.max() < df.index.max() - 1:
        # filer stopped tagging total diluted shares (e.g. per-class EPS only): an old count would
        # miss later splits and break all per-share math, so treat the count as unknown
        df["shares_diluted"] = float("nan")
    # some filers tag share counts in millions/thousands; rescale against the cover-page count
    so = shares_outstanding(facts)
    last_sh = df["shares_diluted"].dropna()
    if so and len(last_sh) and last_sh.iloc[-1] > 0:
        ratio = so / last_sh.iloc[-1]
        for k in (1e6, 1e3):
            if 0.7 * k <= ratio <= 1.4 * k:
                df["shares_diluted"] *= k
                df["eps_diluted"] = df["eps_diluted"]  # per-share values unaffected
                break
    df["cash_all"] = df[["cash", "st_invest"]].fillna(0).sum(axis=1)
    df.index.name = "fy"
    return df


def shares_outstanding(facts: dict) -> float | None:
    """Most recent reported common shares outstanding (cover page), any form."""
    best = None
    for ns, tag in (("dei", "EntityCommonStockSharesOutstanding"), ("us-gaap", "CommonStockSharesOutstanding")):
        rows = facts.get("facts", {}).get(ns, {}).get(tag, {}).get("units", {}).get("shares", [])
        if not rows:
            continue
        # cover pages can list several share classes on the same date: sum those
        latest_end = max(r["end"] for r in rows)
        if latest_end < (date.today().replace(year=date.today().year - 1)).isoformat()[:7]:
            continue  # stale (company stopped tagging it)
        latest = [r for r in rows if r["end"] == latest_end]
        by_filing: dict[str, float] = {}
        for r in latest:
            by_filing[r.get("accn", "")] = by_filing.get(r.get("accn", ""), 0) + float(r["val"])
        val = max(by_filing.values())
        if best is None or latest_end > best[0]:
            best = (latest_end, val)
    return best[1] if best else None
