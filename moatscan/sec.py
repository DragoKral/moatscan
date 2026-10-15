"""SEC EDGAR access: ticker->CIK, XBRL company facts, filing index, raw documents.

SEC requires a descriptive User-Agent with contact email and <=10 requests/sec.
Everything is cached on disk so repeated runs are fast and polite.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

_LAST_CALL = [0.0]
_MIN_GAP = 0.12  # ~8 req/s, under SEC's 10/s limit


class SEC:
    def __init__(self, user_agent: str, cache_dir: str = "data/cache"):
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC needs a User-Agent with a contact email, e.g. 'MoatScan you@example.com' "
                             "(set sec.user_agent in config.yaml or SEC_USER_AGENT env var).")
        self.ua = user_agent
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    # ------------------------------------------------------------ low level
    def _get(self, url: str, binary: bool = False):
        wait = _MIN_GAP - (time.time() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            r = self.s.get(url, timeout=30)
            _LAST_CALL[0] = time.time()
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.content if binary else r.text
        r.raise_for_status()

    def _cached_json(self, name: str, url: str, max_age_h: float):
        p = self.cache / name
        if p.exists() and (time.time() - p.stat().st_mtime) < max_age_h * 3600:
            return json.loads(p.read_text())
        txt = self._get(url)
        p.write_text(txt)
        return json.loads(txt)

    # ------------------------------------------------------------ public
    def cik(self, ticker: str) -> tuple[int, str]:
        m = self._cached_json("company_tickers.json", "https://www.sec.gov/files/company_tickers.json", 24 * 7)
        t = ticker.upper().replace(".", "-")
        for row in m.values():
            if row["ticker"].upper() == t:
                return int(row["cik_str"]), row["title"]
        raise KeyError(f"Ticker {ticker} not found in SEC list")

    def company_facts(self, cik: int) -> dict:
        return self._cached_json(f"facts_{cik:010d}.json",
                                 f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", 24 * 3)

    def submissions(self, cik: int) -> dict:
        return self._cached_json(f"subs_{cik:010d}.json",
                                 f"https://data.sec.gov/submissions/CIK{cik:010d}.json", 12)

    def document(self, cik: int, accession: str, filename: str) -> str:
        acc = accession.replace("-", "")
        p = self.cache / "docs" / f"{cik}_{acc}_{os.path.basename(filename)}"
        if p.exists():
            return p.read_text(errors="ignore")
        txt = self._get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{filename}")
        p.parent.mkdir(exist_ok=True)
        p.write_text(txt)
        return txt
