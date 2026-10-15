# MoatScan: Buffett-style analysis from SEC filings

Reads each company's 10-year XBRL financials straight from SEC EDGAR, the same public records Buffett reads. It answers three questions:

1. **Is it a great business?** It checks:
   - consistent earnings
   - ROIC ≥ 15%
   - ROE without leverage
   - durable margins
   - low debt
   - cash conversion
   - capital-light operations
   - no dilution
   - compounding of per-share value
2. **Are the books honest?** It looks at:
   - accruals
   - receivables and inventory growing faster than sales
   - stock comp
   - goodwill
   - Beneish M-score
   - Piotroski F-score
3. **Does the price match what the business produces?** It works from:
   - **owner earnings**: operating cash − maintenance capex − stock comp
   - a conservative 10-year owner-earnings DCF
   - a **reverse DCF**, which shows the growth the current price already assumes
   - a 30% margin of safety

It also checks **insider open-market buying** from Form 4 filings.

Verdicts:
- **BUY ZONE**: good business at ≥30% below intrinsic value
- **WATCHLIST**: good business, not cheap enough yet
- **AVOID**: fails the quality bar
- **OUTSIDE CIRCLE**: banks, insurers, and REITs don't fit the model

## Setup (Mac)
```bash
cd moatscan
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tests/test_synthetic.py        # offline self-test
```

## Use
```bash
python -m moatscan analyze MU KO AAPL      # deep read; opens an HTML report
python -m moatscan analyze MU --price 95   # override price
python -m moatscan screen                  # ~60-stock universe in config.yaml (~3–5 min first run, cached after)
python -m moatscan screen --no-insiders    # faster
```
Reports and a CSV are saved to `reports/`. All thresholds live in `config.yaml`.

## How to use it well
- Treat **BUY ZONE** as the start of research, not an order. Read the 10-K's business section and ask whether the moat will still exist in 10 years.
- **"Growth price implies"** is the most useful number. If the price needs 15%/yr for a decade and the company has grown 6%, the market is betting on something other than what it has produced.
- Cyclicals (memory chips, autos, oil) are valued on full-cycle average owner earnings. That is why MU tends to fail: its margins and capex swing hard. Buffett avoids that profile.

## Limits
- XBRL tags vary by company. Odd filers can show n/a fields, so check the 10-year history panel.
- Maintenance capex is an estimate (Greenwald method). It is the softest input in the valuation.
- Prices come from yfinance or Stooq. Free sources occasionally break; use `--price` if needed.
- Not financial advice.

## Weekly automation
`./weekly.sh` runs the whole-market scan, writes `reports/market-scan.html` (with changes vs. last week),
and archives the CSV in `history/`. A weekly scheduled Claude task runs it and updates the published page.
