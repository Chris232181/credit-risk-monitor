"""
fetch_bdc_data.py — SEC BDC Data Set & EDGAR XBRL Pipeline

Downloads the latest SEC BDC Data Set (monthly ZIP files) and extracts:
1. SOI (Schedule of Investments) → sector concentrations, top holdings
2. NUM (Numeric data) → total assets, debt, equity, NAV, income
3. SUB (Submissions) → identify which BDCs filed recently

Also queries the EDGAR Company Facts API for supplemental XBRL data.

Output: data/bdc_structured.json with all extractable metrics per fund.
"""

import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    KNOWN_BDCS, SEC_USER_AGENT, SEC_BASE_URL, SEC_BDC_DATA_URL,
    XBRL_TAGS, TECH_KEYWORDS,
)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def sec_request(url, max_retries=3):
    """Make a rate-limited request to SEC with proper User-Agent."""
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip"}
    for attempt in range(max_retries):
        try:
            req = Request(url, headers=headers)
            response = urlopen(req, timeout=30)
            return response
        except HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  HTTP {e.code} for {url}")
                raise
        except URLError as e:
            print(f"  Network error: {e.reason}. Retrying...")
            time.sleep(2)
    raise Exception(f"Failed to fetch {url} after {max_retries} retries")


def download_latest_bdc_dataset():
    """Download the most recent SEC BDC Data Set ZIP file."""
    now = datetime.now()

    # Try current month backwards until we find one
    for months_back in range(6):
        dt = now - timedelta(days=30 * months_back)
        year = dt.year
        month = dt.month

        # SEC uses two URL patterns: YYYY_MM and YYYYqQ
        urls_to_try = [
            f"{SEC_BDC_DATA_URL}/{year}_{month:02d}_bdc.zip",
            f"{SEC_BDC_DATA_URL}/{year}q{((month - 1) // 3) + 1}_bdc.zip",
        ]

        for url in urls_to_try:
            try:
                print(f"  Trying: {url}")
                response = sec_request(url)
                data = response.read()
                print(f"  ✓ Downloaded {len(data)} bytes")
                return data, url
            except Exception:
                continue

    raise Exception("Could not find any recent BDC Data Set")


def parse_tsv_from_zip(zip_data, filename):
    """Extract and parse a TSV file from the BDC ZIP archive."""
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        # Find the file — it might be in a subdirectory
        matching = [n for n in zf.namelist() if n.endswith(filename)]
        if not matching:
            print(f"  ⚠ {filename} not found in ZIP. Available: {zf.namelist()[:10]}")
            return []

        with zf.open(matching[0]) as f:
            text = f.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            return list(reader)


def parse_submissions(sub_rows):
    """Parse SUB table to identify BDC filers and their latest filings."""
    bdc_filings = {}

    for row in sub_rows:
        cik = row.get("cik", "").zfill(10)
        adsh = row.get("adsh", "")
        name = row.get("name", "")
        form = row.get("form", "")
        filed = row.get("filed", "")
        period = row.get("period", "")

        # Only care about 10-K and 10-Q filings
        if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
            continue

        # Track the most recent filing per CIK
        if cik not in bdc_filings or filed > bdc_filings[cik]["filed"]:
            bdc_filings[cik] = {
                "cik": cik,
                "adsh": adsh,
                "name": name,
                "form": form,
                "filed": filed,
                "period": period,
            }

    return bdc_filings


def parse_numeric_data(num_rows, target_ciks, sub_data):
    """Parse NUM table for financial metrics, keyed by CIK."""
    # Build adsh → cik mapping
    adsh_to_cik = {v["adsh"]: k for k, v in sub_data.items()}

    metrics = defaultdict(lambda: defaultdict(dict))

    for row in num_rows:
        adsh = row.get("adsh", "")
        cik = adsh_to_cik.get(adsh)
        if not cik or cik not in target_ciks:
            continue

        tag = row.get("tag", "")
        value = row.get("value", "")
        ddate = row.get("ddate", "")
        segments = row.get("segments", "")

        # Skip dimensional/segmented data for top-level financials
        if segments:
            continue

        try:
            value = float(value)
        except (ValueError, TypeError):
            continue

        # Map XBRL tags to our metric names
        for metric_name, tag_list in XBRL_TAGS.items():
            if tag in tag_list:
                # Keep the most recent value
                if ddate not in metrics[cik][metric_name] or True:
                    metrics[cik][metric_name][ddate] = value

    # Flatten to most recent value per metric
    result = {}
    for cik, metric_dict in metrics.items():
        result[cik] = {}
        for metric_name, date_values in metric_dict.items():
            if date_values:
                latest_date = max(date_values.keys())
                result[cik][metric_name] = date_values[latest_date]

    return result


def parse_schedule_of_investments(soi_rows, target_ciks):
    """Parse SOI table for portfolio concentration analysis."""
    holdings = defaultdict(list)

    for row in soi_rows:
        cik = row.get("cik", "").zfill(10)
        if cik not in target_ciks:
            continue

        sector = row.get("Industry Sector Axis", "") or ""
        identifier = row.get("Investment, Identifier Axis", "") or ""
        inv_type = row.get("Investment Type Axis", "") or ""
        fair_value = row.get("Investment Owned, Fair Value", "")
        cost = row.get("Investment Owned, Cost", "")
        pct_net_assets = row.get("Investment Owned, Net Assets, Percentage", "")
        interest_rate = row.get("Investment Interest Rate", "")
        maturity = row.get("Investment Maturity Date", "")

        try:
            fv = float(fair_value) if fair_value else None
        except (ValueError, TypeError):
            fv = None

        try:
            pct = float(pct_net_assets) if pct_net_assets else None
        except (ValueError, TypeError):
            pct = None

        if fv is not None or pct is not None:
            holdings[cik].append({
                "sector": sector,
                "identifier": identifier,
                "type": inv_type,
                "fair_value": fv,
                "pct_net_assets": pct,
                "interest_rate": interest_rate,
                "maturity": maturity,
            })

    return holdings


def compute_concentration_metrics(holdings_list):
    """Compute tech exposure and top-10 concentration from holdings."""
    if not holdings_list:
        return {"sw_tech_pct": None, "top_10_pct": None, "num_holdings": 0}

    total_fv = sum(h["fair_value"] for h in holdings_list if h["fair_value"])
    if total_fv <= 0:
        return {"sw_tech_pct": None, "top_10_pct": None, "num_holdings": len(holdings_list)}

    # Software/tech exposure
    tech_fv = 0
    for h in holdings_list:
        if h["fair_value"] and h["sector"]:
            sector_lower = h["sector"].lower()
            if any(kw in sector_lower for kw in TECH_KEYWORDS):
                tech_fv += h["fair_value"]

    sw_tech_pct = round((tech_fv / total_fv) * 100, 1) if total_fv else None

    # Top 10 holdings concentration
    sorted_holdings = sorted(
        [h for h in holdings_list if h["fair_value"]],
        key=lambda x: x["fair_value"],
        reverse=True,
    )
    top_10_fv = sum(h["fair_value"] for h in sorted_holdings[:10])
    top_10_pct = round((top_10_fv / total_fv) * 100, 1) if total_fv else None

    return {
        "sw_tech_pct": sw_tech_pct,
        "top_10_pct": top_10_pct,
        "num_holdings": len(holdings_list),
    }


def fetch_company_facts(cik):
    """Fetch XBRL company facts from EDGAR API for a single CIK."""
    url = f"{SEC_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        response = sec_request(url)
        data = json.loads(response.read().decode("utf-8"))
        time.sleep(0.11)  # SEC rate limit: 10 requests/sec
        return data
    except Exception as e:
        print(f"  ⚠ Could not fetch company facts for CIK {cik}: {e}")
        return None


def extract_latest_fact(facts_data, namespace, tag):
    """Extract the most recent value for a given XBRL tag from company facts."""
    if not facts_data:
        return None

    try:
        tag_data = facts_data.get("facts", {}).get(namespace, {}).get(tag, {})
        units = tag_data.get("units", {})

        # Try USD first, then shares, then pure
        for unit_key in ["USD", "shares", "USD/shares", "pure"]:
            if unit_key in units:
                entries = units[unit_key]
                # Filter to 10-K and 10-Q filings
                relevant = [
                    e for e in entries
                    if e.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A")
                ]
                if relevant:
                    latest = max(relevant, key=lambda x: x.get("end", ""))
                    return {
                        "value": latest.get("val"),
                        "end": latest.get("end"),
                        "form": latest.get("form"),
                        "filed": latest.get("filed"),
                    }
    except Exception:
        pass

    return None


def get_xbrl_metrics(cik):
    """Get key financial metrics from EDGAR XBRL for a single BDC."""
    facts = fetch_company_facts(cik)
    if not facts:
        return {}

    metrics = {}

    # Total assets
    for tag in ["Assets"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["total_assets"] = result["value"]
            metrics["total_assets_date"] = result["end"]
            break

    # Net assets (equity)
    for tag in ["NetAssets", "StockholdersEquity"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["net_assets"] = result["value"]
            break

    # Total debt
    for tag in ["LongTermDebt", "DebtInstrumentCarryingAmount", "SecuredDebt"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["total_debt"] = result["value"]
            break

    # NAV per share
    for tag in ["NetAssetValuePerShare"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["nav_per_share"] = result["value"]
            break

    # Shares outstanding
    for tag in ["CommonStockSharesOutstanding"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["shares_outstanding"] = result["value"]
            break

    # Dividends per share
    for tag in ["CommonStockDividendsPerShareDeclared"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["dividends_per_share"] = result["value"]
            break

    # Investment income
    for tag in ["InvestmentIncomeNet", "InvestmentIncomeInterest"]:
        result = extract_latest_fact(facts, "us-gaap", tag)
        if result and result["value"]:
            metrics["investment_income"] = result["value"]
            break

    # Derive D/E ratio and headroom
    if "total_debt" in metrics and "net_assets" in metrics:
        equity = metrics["net_assets"]
        if equity and equity > 0:
            de_ratio = metrics["total_debt"] / equity
            metrics["de_ratio"] = round(de_ratio, 3)
            metrics["headroom_pct"] = round(((2.0 - de_ratio) / 2.0) * 100, 1)

    # Derive AUM (total assets in billions)
    if "total_assets" in metrics:
        metrics["aum_billions"] = round(metrics["total_assets"] / 1e9, 2)

    return metrics


def run_pipeline():
    """Main pipeline: download SEC data, parse, and output structured JSON."""
    print("=" * 60)
    print("Private Credit Fund Risk Monitor — Data Pipeline")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ─── Step 1: Download latest BDC Data Set ───
    print("\n[1/4] Downloading latest SEC BDC Data Set...")
    try:
        zip_data, zip_url = download_latest_bdc_dataset()
        print(f"  Source: {zip_url}")
    except Exception as e:
        print(f"  ✗ Failed to download BDC Data Set: {e}")
        print("  Falling back to EDGAR API only...")
        zip_data = None

    # ─── Step 2: Parse BDC Data Set ───
    all_ciks = set(KNOWN_BDCS.keys())
    sub_data = {}
    soi_holdings = {}
    num_metrics = {}

    if zip_data:
        print("\n[2/4] Parsing BDC Data Set...")

        # Parse submissions
        sub_rows = parse_tsv_from_zip(zip_data, "sub.tsv")
        if sub_rows:
            sub_data = parse_submissions(sub_rows)
            # Add any newly discovered BDCs
            for cik in sub_data:
                all_ciks.add(cik)
            print(f"  Found {len(sub_data)} BDC filings in SUB table")

        # Parse schedule of investments
        soi_rows = parse_tsv_from_zip(zip_data, "soi.tsv")
        if soi_rows:
            soi_holdings = parse_schedule_of_investments(soi_rows, all_ciks)
            print(f"  Parsed SOI data for {len(soi_holdings)} BDCs")

        # Parse numeric data
        num_rows = parse_tsv_from_zip(zip_data, "num.tsv")
        if num_rows:
            num_metrics = parse_numeric_data(num_rows, all_ciks, sub_data)
            print(f"  Parsed NUM data for {len(num_metrics)} BDCs")
    else:
        print("\n[2/4] Skipping BDC Data Set parsing (download failed)")

    # ─── Step 3: Supplement with EDGAR Company Facts API ───
    print(f"\n[3/4] Fetching EDGAR XBRL data for {len(all_ciks)} BDCs...")
    xbrl_data = {}
    for i, cik in enumerate(sorted(all_ciks)):
        label = KNOWN_BDCS.get(cik, {}).get("ticker", cik)
        print(f"  [{i+1}/{len(all_ciks)}] {label}...", end=" ")
        metrics = get_xbrl_metrics(cik)
        if metrics:
            xbrl_data[cik] = metrics
            print(f"✓ ({len(metrics)} metrics)")
        else:
            print("⚠ no data")
        time.sleep(0.11)  # Rate limiting

    # ─── Step 4: Assemble fund records ───
    print(f"\n[4/4] Assembling fund records...")
    funds = []

    for cik in sorted(all_ciks):
        known = KNOWN_BDCS.get(cik, {})
        sub = sub_data.get(cik, {})
        xbrl = xbrl_data.get(cik, {})
        holdings = soi_holdings.get(cik, [])
        concentration = compute_concentration_metrics(holdings)

        fund = {
            "cik": cik,
            "ticker": known.get("ticker", sub.get("name", cik)[:8]),
            "name": known.get("name", sub.get("name", f"CIK {cik}")),
            "type": known.get("type", "Traded BDC"),

            # From XBRL
            "aum_billions": xbrl.get("aum_billions"),
            "total_assets": xbrl.get("total_assets"),
            "total_debt": xbrl.get("total_debt"),
            "net_assets": xbrl.get("net_assets"),
            "de_ratio": xbrl.get("de_ratio"),
            "headroom_pct": xbrl.get("headroom_pct"),
            "nav_per_share": xbrl.get("nav_per_share"),
            "shares_outstanding": xbrl.get("shares_outstanding"),
            "dividends_per_share": xbrl.get("dividends_per_share"),
            "investment_income": xbrl.get("investment_income"),

            # From SOI analysis
            "sw_tech_pct": concentration["sw_tech_pct"],
            "top_10_pct": concentration["top_10_pct"],
            "num_holdings": concentration["num_holdings"],

            # These require text extraction (Claude API) — placeholders
            "non_accrual_pct": None,
            "pik_income_pct": None,
            "redemption_pct_nav": None,
            "gate_status": "None",
            "facility_utilization": None,
            "debt_maturity_12mo_pct": None,
            "qoq_na_change": None,

            # Filing metadata
            "latest_filing_form": sub.get("form"),
            "latest_filing_date": sub.get("filed"),
            "latest_filing_period": sub.get("period"),
            "latest_filing_adsh": sub.get("adsh"),

            # Source tracking
            "data_source": "SEC_EDGAR",
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }

        # Only include funds with at least some data
        has_data = any([
            fund["total_assets"], fund["net_assets"],
            fund["de_ratio"], fund["num_holdings"] > 0
        ])

        if has_data or cik in KNOWN_BDCS:
            funds.append(fund)

    # Sort by AUM descending (None values last)
    funds.sort(key=lambda f: f["aum_billions"] or 0, reverse=True)

    # ─── Save output ───
    output = {
        "generated": datetime.now().isoformat(),
        "source": "SEC EDGAR BDC Data Sets + XBRL Company Facts API",
        "fund_count": len(funds),
        "funds": funds,
    }

    output_path = DATA_DIR / "bdc_structured.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ Saved {len(funds)} fund records to {output_path}")
    print(f"  Funds with XBRL data: {len(xbrl_data)}")
    print(f"  Funds with SOI data: {len(soi_holdings)}")

    return output


if __name__ == "__main__":
    run_pipeline()
