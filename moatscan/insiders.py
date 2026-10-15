"""Insider open-market buying from SEC Form 4 filings.

Only code 'P' (open-market purchase) counts: grants, option exercises and planned
sales say little. Several insiders buying with their own money - especially the CEO
or CFO - is one of the better-documented signals in public filings.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta


def _txt(node, path):
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else ""


def parse_form4(xml: str) -> list[dict]:
    i = xml.find("<ownershipDocument")
    if i < 0:
        return []
    j = xml.find("</ownershipDocument>")
    root = ET.fromstring(xml[i:j + len("</ownershipDocument>")])
    owner = _txt(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    title = _txt(rel, "officerTitle") if rel is not None else ""
    is_dir = _txt(rel, "isDirector") in ("1", "true") if rel is not None else False
    is_off = _txt(rel, "isOfficer") in ("1", "true") if rel is not None else False
    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _txt(t, "transactionCoding/transactionCode")
        ad = _txt(t, "transactionAmounts/transactionAcquiredDisposedCode/value")
        try:
            sh = float(_txt(t, "transactionAmounts/transactionShares/value") or 0)
            px = float(_txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"owner": owner, "title": title, "director": is_dir, "officer": is_off,
                    "date": _txt(t, "transactionDate/value"), "code": code, "ad": ad,
                    "shares": sh, "price": px, "value": sh * px})
    return out


def insider_summary(sec, cik: int, cfg: dict, today: date | None = None) -> dict:
    ic = cfg["insiders"]
    today = today or date.today()
    since = today - timedelta(days=ic["lookback_days"])
    subs = sec.submissions(cik)
    rec = subs.get("filings", {}).get("recent", {})
    rows = zip(rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", []),
               rec.get("primaryDocument", []))
    filings = [(f, dt, a, doc) for f, dt, a, doc in rows if f == "4" and dt >= since.isoformat()]
    filings = filings[: ic["max_filings"]]
    buys, sells = [], 0.0
    for _, _, acc, doc in filings:
        raw_doc = doc.split("/")[-1]  # strip xsl rendering prefix to get raw XML
        try:
            txs = parse_form4(sec.document(cik, acc, raw_doc))
        except Exception:  # noqa: BLE001
            continue
        for t in txs:
            if t["code"] == "P" and t["ad"] == "A":
                buys.append(t)
            elif t["code"] == "S":
                sells += t["value"]
    buyers = {b["owner"] for b in buys}
    top = any(any(k in b["title"].upper() for k in ("CEO", "CHIEF EXECUTIVE", "CFO", "CHIEF FINANCIAL"))
              and b["value"] >= ic["exec_buy_min_usd"] for b in buys)
    total = sum(b["value"] for b in buys)
    signal = len(buyers) >= ic["cluster_min_insiders"] or top
    return {"window_days": ic["lookback_days"], "form4_checked": len(filings), "buyers": sorted(buyers),
            "buy_usd": total, "sell_usd": sells, "exec_buy": top, "cluster": signal,
            "buys": sorted(buys, key=lambda b: b["date"], reverse=True)[:10]}
