#!/usr/bin/env bash
# Weekly market scan: runs the scan, builds the summary page, archives the CSV for next week's comparison.
set -euo pipefail
cd "$(dirname "$0")"
pip install -q --break-system-packages -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt
PREV=$(ls history/*.csv 2>/dev/null | sort | tail -1 || true)
python3 -W ignore -m moatscan scan --no-open 2>&1 | tee scan.log
CSV=$(ls reports/moatscan_scan_*.csv | sort | tail -1)
LISTED=$(grep -oE 'quality on [0-9,]+ listed' scan.log | grep -oE '[0-9,]+' | tr -d ,)
python3 -m moatscan.page "$CSV" reports/market-scan.html --listed "${LISTED:-0}" ${PREV:+--prev "$PREV"}
cp "$CSV" history/
echo "PAGE=reports/market-scan.html CSV=$CSV PREV=${PREV:-none}"
