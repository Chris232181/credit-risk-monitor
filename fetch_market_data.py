"""
fetch_market_data.py — Market Price Data Pipeline

Fetches current market prices and derives P/NAV ratios for traded BDCs.
Uses Financial Modeling Prep (FMP) free API.

If FMP_API_KEY is not set, falls back to SEC EDGAR for last-reported data only.

Output: Enriches data/bdc_structured.json with market data fields.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FMP_BASE_URL, KNOWN_BDCS

DATA_DIR = Path(__file__).parent.parent / "data"


def fetch_fmp_quote(ticker, api_key):
    """Fetch current price quote from FMP."""
    url = f"{FMP_BASE_URL}/quote/{ticker}?apikey={api_key}"
    try:
        req = Request(url, headers={"User-Agent": "PrivateCreditMonitor/1.0"})
        response = urlopen(req, timeout=15)
        data = json.loads(response.read().decode("utf-8"))
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print(f"  ⚠ FMP quote failed for {ticker}: {e}")
    return None


def fetch_fmp_key_metrics(ticker, api_key):
    """Fetch key metrics (includes book value, etc.) from FMP."""
    url = f"{FMP_BASE_URL}/key-metrics/{ticker}?limit=1&apikey={api_key}"
    try:
        req = Request(url, headers={"User-Agent": "PrivateCreditMonitor/1.0"})
        response = urlopen(req, timeout=15)
        data = json.loads(response.read().decode("utf-8"))
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print(f"  ⚠ FMP key metrics failed for {ticker}: {e}")
    return None


def fetch_fmp_dividend_history(ticker, api_key):
    """Fetch recent dividend history from FMP."""
    url = f"{FMP_BASE_URL}/historical-price-full/stock_dividend/{ticker}?apikey={api_key}"
    try:
        req = Request(url, headers={"User-Agent": "PrivateCreditMonitor/1.0"})
        response = urlopen(req, timeout=15)
        data = json.loads(response.read().decode("utf-8"))
        if data and "historical" in data:
            return data["historical"][:8]  # Last 8 dividends
    except Exception as e:
        print(f"  ⚠ FMP dividends failed for {ticker}: {e}")
    return []


def enrich_with_market_data():
    """Load structured BDC data and add market prices + P/NAV."""
    input_path = DATA_DIR / "bdc_structured.json"
    if not input_path.exists():
        print("✗ bdc_structured.json not found. Run fetch_bdc_data.py first.")
        return

    with open(input_path) as f:
        data = json.load(f)

    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        print("⚠ FMP_API_KEY not set. Skipping market data enrichment.")
        print("  Set FMP_API_KEY environment variable for live market prices.")
        print("  Get a free key at: https://financialmodelingprep.com/developer")
        # Still save with null market fields
        for fund in data["funds"]:
            fund.setdefault("market_price", None)
            fund.setdefault("price_nav_ratio", None)
            fund.setdefault("market_cap_millions", None)
            fund.setdefault("dividend_yield", None)
            fund.setdefault("recent_dividends", [])
            fund.setdefault("ytd_return", None)
            fund.setdefault("week_52_high", None)
            fund.setdefault("week_52_low", None)
        with open(input_path, "w") as f:
            json.dump(data, f, indent=2)
        return

    print(f"\nEnriching {len(data['funds'])} funds with market data...")
    enriched_count = 0

    for fund in data["funds"]:
        ticker = fund.get("ticker", "")
        if not ticker or len(ticker) > 6:
            continue

        print(f"  {ticker}...", end=" ")

        # Fetch quote
        quote = fetch_fmp_quote(ticker, api_key)
        time.sleep(0.3)  # Rate limiting for free tier

        if quote:
            fund["market_price"] = quote.get("price")
            fund["market_cap_millions"] = round(
                quote.get("marketCap", 0) / 1e6, 1
            ) if quote.get("marketCap") else None
            fund["ytd_return"] = quote.get("ytd")
            fund["week_52_high"] = quote.get("yearHigh")
            fund["week_52_low"] = quote.get("yearLow")
            fund["dividend_yield"] = round(
                quote.get("dividendYield", 0) * 100, 2
            ) if quote.get("dividendYield") else None

            # Compute P/NAV
            nav = fund.get("nav_per_share")
            price = fund["market_price"]
            if nav and price and nav > 0:
                fund["price_nav_ratio"] = round(price / nav, 3)
            else:
                fund["price_nav_ratio"] = None

            enriched_count += 1
            print(f"✓ ${price}")
        else:
            fund["market_price"] = None
            fund["price_nav_ratio"] = None
            fund["market_cap_millions"] = None
            fund["dividend_yield"] = None
            fund["ytd_return"] = None
            fund["week_52_high"] = None
            fund["week_52_low"] = None
            print("⚠ no data")

        # Fetch dividend history
        divs = fetch_fmp_dividend_history(ticker, api_key)
        fund["recent_dividends"] = [
            {"date": d.get("date"), "amount": d.get("dividend")}
            for d in divs
        ]
        time.sleep(0.3)

    # Save enriched data
    data["market_data_updated"] = datetime.now().isoformat()
    with open(input_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n✓ Enriched {enriched_count}/{len(data['funds'])} funds with market data")


if __name__ == "__main__":
    enrich_with_market_data()
