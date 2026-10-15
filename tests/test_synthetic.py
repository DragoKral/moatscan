"""Offline test: builds SEC-format company facts / Form 4 data for two made-up companies
and runs the full pipeline. Run:  python tests/test_synthetic.py"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from moatscan.analysis import analyze  # noqa: E402
from moatscan.fundamentals import annual_table, shares_outstanding  # noqa: E402
from moatscan.insiders import insider_summary, parse_form4  # noqa: E402
from moatscan.report import render  # noqa: E402

CFG = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "config.yaml"))


def facts_for(series: dict, shares_out: float, rev_tag_switch_year=2019):
    """series: field_tag -> {year: value}; flows get Jan1-Dec31 ranges; adds noise rows."""
    flows = {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "CostOfRevenue", "GrossProfit",
             "OperatingIncomeLoss", "NetIncomeLoss", "IncomeTaxExpenseBenefit", "InterestExpense",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
             "DepreciationDepletionAndAmortization", "ShareBasedCompensation", "PaymentsOfDividends",
             "PaymentsForRepurchaseOfCommonStock", "WeightedAverageNumberOfDilutedSharesOutstanding",
             "SellingGeneralAndAdministrativeExpense"}
    g = {}
    for tag, vals in series.items():
        unit = "shares" if "Shares" in tag else "USD"
        rows = []
        for y, v in vals.items():
            t = tag
            if tag == "Revenues" and y >= rev_tag_switch_year:
                t = "RevenueFromContractWithCustomerExcludingAssessedTax"
            row = {"end": f"{y}-12-31", "val": v, "accn": f"0000-{y}", "fy": y, "fp": "FY",
                   "form": "10-K", "filed": f"{y+1}-02-15"}
            if tag in flows:
                row["start"] = f"{y}-01-01"
                # a quarterly row that must be ignored
                rows.append({**row, "start": f"{y}-10-01", "val": v / 4, "form": "10-Q", "fp": "Q4"})
            # same period repeated as comparative in next year's 10-K (older filed date wins less)
            rows.append({**row, "filed": f"{y+2}-02-15", "accn": f"0000-{y+1}c"})
            rows.append(row)
            g.setdefault(t, {"units": {unit: []}})["units"][unit].append(rows[-1])
            g[t]["units"][unit].extend(rows[:-1])
            rows = []
    return {"cik": 1, "entityName": "X", "facts": {"us-gaap": g, "dei": {"EntityCommonStockSharesOutstanding": {
        "units": {"shares": [{"end": "2026-02-01", "val": shares_out, "accn": "a", "form": "10-K"}]}}}}}


def moat_co():
    yrs = range(2014, 2026)
    rev = {y: 10e9 * 1.08 ** (y - 2014) for y in yrs}
    sh = {y: 1e9 * 0.98 ** (y - 2014) for y in yrs}
    s = {
        "Revenues": rev,
        "CostOfRevenue": {y: rev[y] * 0.40 for y in yrs},
        "SellingGeneralAndAdministrativeExpense": {y: rev[y] * 0.25 for y in yrs},
        "OperatingIncomeLoss": {y: rev[y] * 0.30 for y in yrs},
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {y: rev[y] * 0.29 for y in yrs},
        "IncomeTaxExpenseBenefit": {y: rev[y] * 0.29 * 0.2 for y in yrs},
        "NetIncomeLoss": {y: rev[y] * 0.29 * 0.8 for y in yrs},
        "InterestExpense": {y: 0.1e9 for y in yrs},
        "NetCashProvidedByUsedInOperatingActivities": {y: rev[y] * 0.27 for y in yrs},
        "PaymentsToAcquirePropertyPlantAndEquipment": {y: rev[y] * 0.04 for y in yrs},
        "DepreciationDepletionAndAmortization": {y: rev[y] * 0.035 for y in yrs},
        "ShareBasedCompensation": {y: rev[y] * 0.01 for y in yrs},
        "PaymentsOfDividends": {y: rev[y] * 0.08 for y in yrs},
        "PaymentsForRepurchaseOfCommonStock": {y: rev[y] * 0.10 for y in yrs},
        "WeightedAverageNumberOfDilutedSharesOutstanding": sh,
        "Assets": {y: rev[y] * 1.2 for y in yrs},
        "AssetsCurrent": {y: rev[y] * 0.5 for y in yrs},
        "LiabilitiesCurrent": {y: rev[y] * 0.25 for y in yrs},
        "Liabilities": {y: rev[y] * 0.6 for y in yrs},
        "StockholdersEquity": {y: rev[y] * 0.6 for y in yrs},
        "CashAndCashEquivalentsAtCarryingValue": {y: rev[y] * 0.2 for y in yrs},
        "LongTermDebtNoncurrent": {y: 2e9 for y in yrs},
        "AccountsReceivableNetCurrent": {y: rev[y] * 0.1 for y in yrs},
        "InventoryNet": {y: rev[y] * 0.08 for y in yrs},
        "PropertyPlantAndEquipmentNet": {y: rev[y] * 0.3 for y in yrs},
        "Goodwill": {y: rev[y] * 0.1 for y in yrs},
    }
    return facts_for(s, sh[2025])


def cyclical_co():
    yrs = list(range(2014, 2026))
    cyc = [1.0, 0.8, 0.7, 1.2, 1.6, 1.0, 1.1, 1.4, 1.5, 0.75, 1.1, 1.9]
    rev = {y: 15e9 * c for y, c in zip(yrs, cyc)}
    om = {y: (c - 0.85) * 0.6 for y, c in zip(yrs, cyc)}
    sh = {y: 1.1e9 * 1.005 ** (y - 2014) for y in yrs}
    s = {
        "Revenues": rev,
        "CostOfRevenue": {y: rev[y] * (0.75 - om[y] * 0.5) for y in yrs},
        "SellingGeneralAndAdministrativeExpense": {y: rev[y] * 0.08 for y in yrs},
        "OperatingIncomeLoss": {y: rev[y] * om[y] for y in yrs},
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {y: rev[y] * om[y] for y in yrs},
        "IncomeTaxExpenseBenefit": {y: max(rev[y] * om[y] * 0.12, 0) for y in yrs},
        "NetIncomeLoss": {y: rev[y] * om[y] * 0.88 for y in yrs},
        "InterestExpense": {y: 0.3e9 for y in yrs},
        "NetCashProvidedByUsedInOperatingActivities": {y: rev[y] * (0.25 + om[y]) for y in yrs},
        "PaymentsToAcquirePropertyPlantAndEquipment": {y: rev[y] * 0.35 for y in yrs},
        "DepreciationDepletionAndAmortization": {y: rev[y] * 0.22 for y in yrs},
        "ShareBasedCompensation": {y: rev[y] * 0.03 for y in yrs},
        "WeightedAverageNumberOfDilutedSharesOutstanding": sh,
        "Assets": {y: 50e9 for y in yrs},
        "AssetsCurrent": {y: 18e9 for y in yrs},
        "LiabilitiesCurrent": {y: 7e9 for y in yrs},
        "Liabilities": {y: 20e9 for y in yrs},
        "StockholdersEquity": {y: 30e9 + (y - 2014) * 3e9 for y in yrs},
        "CashAndCashEquivalentsAtCarryingValue": {y: 8e9 for y in yrs},
        "LongTermDebtNoncurrent": {y: 12e9 for y in yrs},
        "AccountsReceivableNetCurrent": {y: rev[y] * 0.15 for y in yrs},
        "InventoryNet": {y: rev[y] * 0.25 for y in yrs},
        "PropertyPlantAndEquipmentNet": {y: 35e9 for y in yrs},
    }
    return facts_for(s, sh[2025])


FORM4 = """<?xml version="1.0"?><ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>{n}</rptOwnerName>
</reportingOwnerId><reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
<officerTitle>{t}</officerTitle></reportingOwnerRelationship></reportingOwner><nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-09-10</value></transactionDate><transactionCoding>
<transactionCode>P</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>2000</value>
</transactionShares><transactionPricePerShare><value>80.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


class FakeSEC:
    def submissions(self, cik):
        return {"filings": {"recent": {"form": ["4", "4", "10-Q", "4"],
                                       "filingDate": ["2026-09-12", "2026-09-11", "2026-08-01", "2025-01-01"],
                                       "accessionNumber": ["a1", "a2", "a3", "a4"],
                                       "primaryDocument": ["xslF345X05/f1.xml", "xslF345X05/f2.xml", "q.htm", "f4.xml"]}}}

    def document(self, cik, acc, fn):
        return FORM4.format(n={"f1.xml": "Jane Doe", "f2.xml": "John Roe"}[fn],
                            t={"f1.xml": "Chief Executive Officer", "f2.xml": "CFO"}[fn])


def main():
    from datetime import date
    results = []
    for tk, f, px in (("MOAT", moat_co(), 210.0), ("CYCL", cyclical_co(), 95.0)):
        df = annual_table(f)
        assert len(df) == 12, len(df)
        assert df["revenue"].notna().all(), "revenue tag switch not merged"
        so = shares_outstanding(f)
        r = analyze(tk, f"{tk} Corp (synthetic)", df, so, px, CFG, 0.043)
        if tk == "MOAT":
            r.insiders = insider_summary(FakeSEC(), 1, CFG, today=date(2026, 10, 14))
        results.append(r)
        v = r.valuation
        print(f"{tk}: {r.verdict} | quality {r.quality_score:.0f} | F={r.piotroski} | M={r.beneish:.2f} | "
              f"IV/sh {v.get('iv_per_share'):.2f} vs {px} | MoS {v.get('margin_of_safety'):+.0%} | "
              f"implied g {v.get('implied_growth'):.1%} | cyclical {v['cyclical']}")
        for c in r.quality:
            print("   ", "PASS" if c.passed else "FAIL" if c.passed is False else "n/a ", c.name, "-", c.value)
        print("    flags:", [c.name for c in r.flags if c.passed])
    ins = results[0].insiders
    assert ins["form4_checked"] == 2 and ins["exec_buy"] and ins["cluster"], ins
    assert parse_form4("<html>no xml</html>") == []
    m, c = results
    assert m.quality_score > c.quality_score
    assert c.valuation["cyclical"] and not m.valuation["cyclical"]
    assert not math.isnan(m.valuation["iv_per_share"])
    Path("reports").mkdir(exist_ok=True)
    Path("reports/synthetic_test.html").write_text(render(results, "MoatScan synthetic test"))
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
