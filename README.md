# Private Credit Fund Risk Monitor

A self-updating dashboard that tracks risk metrics for traded BDCs (Business Development Companies), sourcing data directly from SEC EDGAR filings.

**Live dashboard:** `https://yourusername.github.io/credit-risk-monitor/`

## Architecture

```
Monthly GitHub Actions cron (1st of each month)
    │
    ├── 1. Download SEC BDC Data Set (SOI + NUM tables)
    ├── 2. Query EDGAR XBRL Company Facts API
    ├── 3. Fetch market prices (FMP free API)
    ├── 4. Extract unstructured metrics (Claude API / Haiku)
    ├── 5. Compute composite risk scores (6-factor model)
    ├── 6. Write data/funds.json
    └── 7. Auto-commit → GitHub Pages deploys
```

## Data Sources

| Source | Cost | Metrics |
|--------|------|---------|
| SEC BDC Data Sets | Free | Portfolio holdings, sector concentrations, fair values |
| EDGAR XBRL API | Free | Total assets, debt, equity, D/E ratio, NAV, dividends |
| Financial Modeling Prep | Free tier | Market price, P/NAV, dividend yield, YTD return |
| Claude API (Haiku) | ~$1-5/mo | Non-accrual %, PIK %, redemption %, gate status |

## Quick Start

### 1. Fork/clone this repo
```bash
git clone https://github.com/yourusername/credit-risk-monitor.git
cd credit-risk-monitor
```

### 2. Update config
Edit `scripts/config.py`:
- Change `SEC_USER_AGENT` to include your email (SEC requires this)
- Review the `KNOWN_BDCS` list and add/remove tickers

### 3. Set up API keys (optional but recommended)

In your GitHub repo → Settings → Secrets and variables → Actions:

- `FMP_API_KEY` — Get free at [financialmodelingprep.com/developer](https://financialmodelingprep.com/developer)
- `ANTHROPIC_API_KEY` — Get at [console.anthropic.com](https://console.anthropic.com)

The pipeline works without these keys but will have gaps in market data and unstructured metrics.

### 4. Enable GitHub Pages
- Go to Settings → Pages
- Source: Deploy from branch
- Branch: `main`, folder: `/ (root)`

### 5. Run the pipeline
- Go to Actions tab → "Update BDC Risk Data" → "Run workflow"
- Or wait for the monthly cron (1st of each month, 8am UTC)

### 6. Manual data entry
Edit `data/manual_overrides.json` to add governance scores, analyst commentary, or correct any automated data.

## Running Locally

```bash
# Fetch SEC data
python scripts/fetch_bdc_data.py

# Add market prices (requires FMP_API_KEY env var)
FMP_API_KEY=your_key python scripts/fetch_market_data.py

# Extract text metrics (requires ANTHROPIC_API_KEY env var)
ANTHROPIC_API_KEY=your_key python scripts/extract_filing_text.py

# Score funds
python scripts/score_funds.py

# Serve locally
python -m http.server 8000
# Open http://localhost:8000
```

## Scoring Methodology

6-factor composite model (0-100 scale):

| Factor | Weight | Sub-metrics |
|--------|--------|-------------|
| Redemption Pressure | 25% | Redemption rate, gate status, unmet queue |
| Leverage & Headroom | 20% | D/E ratio, regulatory headroom, near-term maturities |
| Credit Quality | 20% | Non-accrual rate, PIK income %, QoQ change |
| Liquidity Adequacy | 15% | Liquidity/AUM, facility utilization |
| Concentration Risk | 10% | Software/tech exposure, top-10 holdings |
| Sponsor & Governance | 10% | Sponsor tier, balance sheet support, board independence |

Rating scale: Critical (76-100) → High (56-75) → Elevated (36-55) → Moderate (16-35) → Low (0-15)

## Disclaimer

This is an analytical reference tool for informational purposes only. It does not constitute investment advice. All data should be verified against primary SEC filings before use in investment decisions.
