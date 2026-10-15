"""Latest share price and 10-year Treasury yield from free sources (best effort)."""
from __future__ import annotations

import csv
import io

import requests

UA = {"User-Agent": "Mozilla/5.0"}


def price(ticker: str) -> float | None:
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        try:
            p = float(tk.fast_info["last_price"])
            if p == p and p > 0:
                return p
        except Exception:  # noqa: BLE001
            pass
        h = tk.history(period="5d")["Close"].dropna()
        if len(h):
            return float(h.iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    try:
        url = f"https://stooq.com/q/l/?s={ticker.lower()}.us&f=sd2t2ohlcv&h&e=csv"
        rows = list(csv.DictReader(io.StringIO(requests.get(url, headers=UA, timeout=15).text)))
        c = rows[0].get("Close")
        return float(c) if c and c != "N/D" and float(c) > 0 else None
    except Exception:  # noqa: BLE001
        return None


def treasury_10y(default: float) -> float:
    try:
        txt = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", headers=UA, timeout=15).text
        for line in reversed(txt.strip().splitlines()):
            v = line.split(",")[-1]
            if v not in (".", "", "DGS10"):
                return float(v) / 100
    except Exception:  # noqa: BLE001
        pass
    return default


def analyst_growth(ticker: str) -> float | None:
    """Average analyst revenue growth forecast for this fiscal year and next (Yahoo consensus)."""
    try:
        import yfinance as yf
        re = yf.Ticker(ticker).revenue_estimate
        if re is None or "growth" not in re:
            return None
        vals = [float(re.loc[k, "growth"]) for k in ("0y", "+1y") if k in re.index and re.loc[k, "growth"] == re.loc[k, "growth"]]
        return sum(vals) / len(vals) if vals else None
    except Exception:  # noqa: BLE001
        return None


def radar(ticker: str) -> dict:
    """How widely followed a stock is and how beaten down: analyst count, ownership, distance from 52-week high."""
    out = {}
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        out["analysts"] = info.get("numberOfAnalystOpinions") or 0
        out["inst_own"] = info.get("heldPercentInstitutions")
        out["insider_own"] = info.get("heldPercentInsiders")
        hi, px = info.get("fiftyTwoWeekHigh"), info.get("currentPrice") or info.get("regularMarketPrice")
        if hi and px:
            out["off_high"] = 1 - px / hi
    except Exception:  # noqa: BLE001
        pass
    return out
