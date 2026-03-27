"""
extract_filing_text.py — Claude API Unstructured Metric Extraction

For metrics not available in structured XBRL data, this script:
1. Downloads the full text of each BDC's latest 10-K or 10-Q from EDGAR
2. Sends relevant sections to Claude API (Haiku) for extraction
3. Extracts: non-accrual %, PIK income %, redemption %, gate status,
   facility utilization %, and near-term debt maturity %

Requires ANTHROPIC_API_KEY environment variable.
Cost: ~$0.10-0.30 per fund per extraction (Haiku pricing).

Output: Enriches data/bdc_structured.json with extracted metrics.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ANTHROPIC_MODEL, SEC_USER_AGENT

DATA_DIR = Path(__file__).parent.parent / "data"

EXTRACTION_PROMPT = """You are a financial analyst extracting specific metrics from a BDC (Business Development Company) SEC filing. Analyze the filing text below and extract the following metrics. Return ONLY a JSON object with these fields — no other text, no markdown backticks:

{
  "non_accrual_pct": <number or null>,
  "pik_income_pct": <number or null>,
  "redemption_pct_nav": <number or null>,
  "gate_status": "<None|Partial|Full|Closed>",
  "facility_utilization_pct": <number or null>,
  "debt_maturity_12mo_pct": <number or null>,
  "qoq_na_change_pp": <number or null>,
  "extraction_notes": "<brief notes on data quality or caveats>"
}

Definitions:
- non_accrual_pct: Fair value of non-accrual investments / total portfolio fair value, as a percentage
- pik_income_pct: Payment-in-kind interest income / total investment income, as a percentage
- redemption_pct_nav: Total redemptions or share repurchases in the period as a % of NAV (for traded BDCs this is share buybacks)
- gate_status: Whether any redemption restrictions are in place
- facility_utilization_pct: Amount drawn on credit facility / total facility commitment, as a percentage
- debt_maturity_12mo_pct: Percentage of total debt maturing within 12 months of the filing date
- qoq_na_change_pp: Quarter-over-quarter change in non-accrual rate in percentage points

If a metric is not mentioned or cannot be determined from the text, use null. Be precise with numbers — use the exact figures from the filing."""


def fetch_filing_text(adsh, max_chars=80000):
    """Download the full text of an EDGAR filing by accession number."""
    # Convert adsh format (0000000000-00-000000) to URL path
    adsh_clean = adsh.replace("-", "")
    adsh_path = adsh

    # Fetch filing index to find the main document
    index_url = f"https://www.sec.gov/Archives/edgar/data/{adsh_clean[:10].lstrip('0')}/{''.join(adsh.split('-'))}/{adsh}-index.htm"

    # Try the direct full submission text
    txt_url = f"https://www.sec.gov/Archives/edgar/data/{adsh_clean[:10].lstrip('0')}/{adsh_clean}/{adsh}.txt"

    # Actually, use the EDGAR full-text search to get the filing
    # Simpler: use the filing viewer
    viewer_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{adsh}%22&dateRange=custom&startdt=2024-01-01&enddt=2026-12-31"

    # Most reliable: fetch the primary document from the index
    try:
        headers = {"User-Agent": SEC_USER_AGENT}
        idx_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&accession={adsh}&type=&dateb=&owner=include&count=1&search_text=&action=getcompany"

        # Direct approach: fetch the inline XBRL viewer which has the text
        cik = adsh_clean[:10]
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh_clean}/"

        req = Request(doc_url, headers=headers)
        response = urlopen(req, timeout=30)
        index_html = response.read().decode("utf-8", errors="replace")

        # Find the main 10-K or 10-Q document link
        # Look for .htm files that are the main document
        doc_links = re.findall(r'href="([^"]*(?:10-?[kq]|annual|quarterly)[^"]*\.htm)"', index_html, re.IGNORECASE)
        if not doc_links:
            # Try any .htm file
            doc_links = re.findall(r'href="([^"]*\.htm)"', index_html)

        if doc_links:
            main_doc = doc_links[0]
            if not main_doc.startswith("http"):
                main_doc = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh_clean}/{main_doc}"

            req2 = Request(main_doc, headers=headers)
            resp2 = urlopen(req2, timeout=60)
            filing_html = resp2.read().decode("utf-8", errors="replace")

            # Strip HTML tags for text extraction
            text = re.sub(r'<[^>]+>', ' ', filing_html)
            text = re.sub(r'\s+', ' ', text)

            # Truncate to max chars
            return text[:max_chars]

    except Exception as e:
        print(f"    ⚠ Could not fetch filing text: {e}")

    return None


def extract_relevant_sections(full_text):
    """Extract the most relevant sections for metric extraction."""
    if not full_text:
        return ""

    sections_of_interest = []
    text_lower = full_text.lower()

    # Look for key sections
    keywords = [
        "non-accrual", "nonaccrual", "non accrual",
        "payment-in-kind", "payment in kind", "pik",
        "credit facility", "revolving", "borrowing",
        "redemption", "repurchase", "share buyback",
        "maturity", "maturities",
        "asset quality", "portfolio quality",
        "investment income",
    ]

    # Extract ~2000 chars around each keyword occurrence
    for kw in keywords:
        idx = text_lower.find(kw)
        while idx != -1:
            start = max(0, idx - 500)
            end = min(len(full_text), idx + 1500)
            sections_of_interest.append(full_text[start:end])
            idx = text_lower.find(kw, idx + len(kw) + 100)

    if sections_of_interest:
        # Deduplicate and combine, cap at ~40K chars for Claude
        combined = "\n---\n".join(sections_of_interest)
        return combined[:40000]

    # Fallback: return first 40K chars
    return full_text[:40000]


def call_claude_api(text, ticker):
    """Send filing text to Claude API for metric extraction."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    import json as json_module
    from urllib.request import Request, urlopen

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1000,
        "messages": [
            {
                "role": "user",
                "content": f"Filing text for {ticker}:\n\n{text}\n\n{EXTRACTION_PROMPT}"
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    try:
        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=json_module.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        response = urlopen(req, timeout=60)
        result = json_module.loads(response.read().decode("utf-8"))

        # Extract text content
        content = result.get("content", [])
        for block in content:
            if block.get("type") == "text":
                text_response = block["text"].strip()
                # Clean up any markdown backticks
                text_response = re.sub(r'^```json\s*', '', text_response)
                text_response = re.sub(r'\s*```$', '', text_response)
                return json_module.loads(text_response)

    except Exception as e:
        print(f"    ⚠ Claude API error for {ticker}: {e}")

    return None


def run_extraction():
    """Main extraction pipeline."""
    input_path = DATA_DIR / "bdc_structured.json"
    if not input_path.exists():
        print("✗ bdc_structured.json not found. Run fetch_bdc_data.py first.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("⚠ ANTHROPIC_API_KEY not set. Skipping text extraction.")
        print("  Set ANTHROPIC_API_KEY for non-accrual, PIK, redemption data.")
        return

    with open(input_path) as f:
        data = json.load(f)

    print(f"\nExtracting unstructured metrics for {len(data['funds'])} funds...")
    extracted_count = 0

    for fund in data["funds"]:
        ticker = fund.get("ticker", "")
        adsh = fund.get("latest_filing_adsh")

        if not adsh:
            print(f"  {ticker}: no filing ADSH, skipping")
            continue

        print(f"  {ticker} (ADSH: {adsh})...")

        # Step 1: Download filing text
        print(f"    Downloading filing text...")
        filing_text = fetch_filing_text(adsh)
        time.sleep(0.2)

        if not filing_text:
            print(f"    ⚠ Could not retrieve filing text")
            continue

        # Step 2: Extract relevant sections
        relevant_text = extract_relevant_sections(filing_text)
        print(f"    Extracted {len(relevant_text)} chars of relevant text")

        # Step 3: Send to Claude API
        print(f"    Calling Claude API...")
        extracted = call_claude_api(relevant_text, ticker)

        if extracted:
            # Merge extracted metrics into fund record
            for key in ["non_accrual_pct", "pik_income_pct", "redemption_pct_nav",
                         "gate_status", "facility_utilization", "debt_maturity_12mo_pct",
                         "qoq_na_change"]:
                api_key_name = key
                if key == "facility_utilization":
                    api_key_name = "facility_utilization_pct"
                if key == "qoq_na_change":
                    api_key_name = "qoq_na_change_pp"

                if api_key_name in extracted and extracted[api_key_name] is not None:
                    fund[key] = extracted[api_key_name]

            fund["extraction_notes"] = extracted.get("extraction_notes", "")
            fund["text_extraction_date"] = datetime.now().strftime("%Y-%m-%d")
            extracted_count += 1
            print(f"    ✓ Extracted metrics")
        else:
            print(f"    ⚠ Extraction failed")

        time.sleep(1)  # Rate limiting

    # Save enriched data
    data["text_extraction_updated"] = datetime.now().isoformat()
    with open(input_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n✓ Extracted unstructured metrics for {extracted_count}/{len(data['funds'])} funds")


if __name__ == "__main__":
    run_extraction()
