"""
Configuration for the Private Credit Fund Risk Monitor.

- KNOWN_BDCS: Maps CIK numbers to metadata for major traded BDCs.
  The pipeline also auto-discovers any BDC in the SEC BDC Data Sets,
  so this list supplements (not limits) the universe.
- SCORING_THRESHOLDS: Defines the composite risk scoring model.
- API config and constants.
"""

# ═══════════════════════════════════════════════════════════
#  KNOWN TRADED BDCs — CIK → metadata
#  CIKs sourced from SEC EDGAR. Add new BDCs here as needed.
#  The pipeline will also auto-discover BDCs from SEC data sets.
# ═══════════════════════════════════════════════════════════
KNOWN_BDCS = {
    "0000819793": {"ticker": "ARCC", "name": "Ares Capital Corp", "type": "Traded BDC"},
    "0001396440": {"ticker": "MAIN", "name": "Main Street Capital Corp", "type": "Traded BDC"},
    "0001544206": {"ticker": "OBDC", "name": "Blue Owl Capital Corp", "type": "Traded BDC"},
    "0001422559": {"ticker": "FSK", "name": "FS KKR Capital Corp", "type": "Traded BDC"},
    "0001280361": {"ticker": "HTGC", "name": "Hercules Capital Inc", "type": "Traded BDC"},
    "0001655050": {"ticker": "BXSL", "name": "Blackstone Secured Lending Fund", "type": "Traded BDC"},
    "0001572694": {"ticker": "GBDC", "name": "Golub Capital BDC Inc", "type": "Traded BDC"},
    "0001287750": {"ticker": "PSEC", "name": "Prospect Capital Corp", "type": "Traded BDC"},
    "0001655888": {"ticker": "ORCC", "name": "Owl Rock Capital Corp", "type": "Traded BDC"},
    "0001611988": {"ticker": "MFIC", "name": "MidCap Financial Investment Corp", "type": "Traded BDC"},
    "0001376502": {"ticker": "TPVG", "name": "TriplePoint Venture Growth BDC Corp", "type": "Traded BDC"},
    "0001379785": {"ticker": "BBDC", "name": "Barings BDC Inc", "type": "Traded BDC"},
    "0001490349": {"ticker": "TSLX", "name": "Sixth Street Specialty Lending Inc", "type": "Traded BDC"},
    "0001418076": {"ticker": "GSBD", "name": "Goldman Sachs BDC Inc", "type": "Traded BDC"},
    "0001512931": {"ticker": "NMFC", "name": "New Mountain Finance Corp", "type": "Traded BDC"},
    "0001633932": {"ticker": "OCSL", "name": "Oaktree Specialty Lending Corp", "type": "Traded BDC"},
    "0001268884": {"ticker": "CSWC", "name": "Capital Southwest Corp", "type": "Traded BDC"},
    "0001347652": {"ticker": "FDUS", "name": "Fidus Investment Corp", "type": "Traded BDC"},
    "0001503401": {"ticker": "SLRC", "name": "SLR Investment Corp", "type": "Traded BDC"},
    "0001568651": {"ticker": "CCAP", "name": "Crescent Capital BDC Inc", "type": "Traded BDC"},
    "0001580345": {"ticker": "BCSF", "name": "Bain Capital Specialty Finance Inc", "type": "Traded BDC"},
    "0001550913": {"ticker": "CGBD", "name": "Carlyle Secured Lending Inc", "type": "Traded BDC"},
    "0001655051": {"ticker": "OBDE", "name": "Blue Owl Capital Corp II", "type": "Traded BDC"},
    "0001392857": {"ticker": "GLAD", "name": "Gladstone Investment Corp", "type": "Traded BDC"},
    "0001273931": {"ticker": "GAIN", "name": "Gladstone Capital Corp", "type": "Traded BDC"},
    "0001504619": {"ticker": "TCPC", "name": "BlackRock TCP Capital Corp", "type": "Traded BDC"},
    "0001487918": {"ticker": "PNNT", "name": "PennantPark Floating Rate Capital Ltd", "type": "Traded BDC"},
    "0001386195": {"ticker": "PFLT", "name": "PennantPark Floating Rate Capital", "type": "Traded BDC"},
    "0001091748": {"ticker": "WHF", "name": "WhiteHorse Finance Inc", "type": "Traded BDC"},
    "0001580149": {"ticker": "HRZN", "name": "Horizon Technology Finance Corp", "type": "Traded BDC"},
}

# SEC EDGAR API configuration
SEC_USER_AGENT = "chris.stracke1@gmail.com"  # CHANGE THIS to your email
SEC_BASE_URL = "https://data.sec.gov"
SEC_EDGAR_SEARCH = "https://efts.sec.gov/LATEST"
SEC_BDC_DATA_URL = "https://www.sec.gov/files/structureddata/data/business-development-company-bdc-data-sets"

# FMP (Financial Modeling Prep) free API
FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"
FMP_API_KEY = ""  # Set via environment variable FMP_API_KEY

# Anthropic Claude API (for unstructured text extraction)
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # Cheapest model, sufficient for extraction

# ═══════════════════════════════════════════════════════════
#  COMPOSITE RISK SCORING THRESHOLDS
#  Each sub-metric maps a raw value to a 0-100 risk score.
#  Higher score = higher risk.
# ═══════════════════════════════════════════════════════════
SCORING_WEIGHTS = {
    "redemption": 0.25,
    "leverage": 0.20,
    "credit": 0.20,
    "liquidity": 0.15,
    "concentration": 0.10,
    "governance": 0.10,
}

# Thresholds: [low_cutoff, mid_cutoff, high_cutoff]
# Values at or below low_cutoff score ~12.5 (low risk)
# Values above high_cutoff score ~87.5 (critical risk)
SCORING_THRESHOLDS = {
    # Factor 1: Redemption Pressure
    "redemption_rate": [2, 5, 10],          # % of NAV
    "unmet_queue": [0, 2, 5],               # % of NAV

    # Factor 2: Leverage & Headroom
    "de_ratio": [0.75, 1.0, 1.5],           # x
    "headroom": [50, 30, 15],               # % (inverse — higher is safer)
    "debt_maturity_12mo": [10, 25, 50],     # %

    # Factor 3: Credit Quality
    "non_accrual": [1.5, 3.0, 6.0],         # %
    "pik_income": [5, 10, 15],              # %
    "qoq_na_change": [0, 0.5, 1.5],         # pp

    # Factor 4: Liquidity Adequacy
    "liq_aum": [15, 10, 5],                 # % (inverse)
    "facility_utilization": [40, 60, 80],    # %

    # Factor 5: Concentration Risk
    "sw_tech_exposure": [15, 25, 40],        # %
    "top_10_holdings": [15, 25, 35],         # %
}

# Rating scale
RATING_SCALE = [
    (76, 100, "critical", "Critical"),
    (56, 75, "high", "High"),
    (36, 55, "elevated", "Elevated"),
    (16, 35, "moderate", "Moderate"),
    (0, 15, "low", "Low"),
]

# XBRL tags commonly used by BDCs for key financial metrics
XBRL_TAGS = {
    "total_assets": [
        "Assets",
        "us-gaap:Assets",
    ],
    "total_debt": [
        "LongTermDebt",
        "DebtInstrumentCarryingAmount",
        "LongTermDebtAndCapitalLeaseObligations",
        "us-gaap:LongTermDebt",
        "SecuredDebt",
        "UnsecuredDebt",
    ],
    "total_equity": [
        "StockholdersEquity",
        "NetAssetsOrEquity",
        "us-gaap:StockholdersEquity",
    ],
    "net_assets": [
        "NetAssets",
        "us-gaap:NetAssets",
    ],
    "nav_per_share": [
        "NetAssetValuePerShare",
        "NetAssetValuePerUnit",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "us-gaap:CommonStockSharesOutstanding",
    ],
    "total_investment_income": [
        "InvestmentIncomeNet",
        "TotalInvestmentIncome",
        "InvestmentIncomeInterest",
    ],
    "dividends_per_share": [
        "CommonStockDividendsPerShareDeclared",
        "us-gaap:CommonStockDividendsPerShareDeclared",
    ],
}

# Industry keywords for software/tech classification in SOI data
TECH_KEYWORDS = [
    "software", "saas", "technology", "digital", "cyber",
    "cloud", "data", "internet", "ai ", "artificial intelligence",
    "machine learning", "it services", "information technology",
    "tech", "platform", "app ", "application",
]

