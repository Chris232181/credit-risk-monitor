"""
score_funds.py — Composite Risk Scoring Engine

Reads data/bdc_structured.json and computes composite risk scores
for each fund using the 6-factor, 17-sub-metric model.

Outputs: data/funds.json (final file consumed by the dashboard)
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SCORING_WEIGHTS, SCORING_THRESHOLDS, RATING_SCALE

DATA_DIR = Path(__file__).parent.parent / "data"


def score_metric(value, thresholds, inverse=False):
    """
    Score a single metric on a 0-100 scale.
    thresholds = [low, mid, high] cutoffs.
    For normal metrics: higher value = higher risk.
    For inverse metrics (headroom, liquidity): higher value = lower risk.
    """
    if value is None:
        return None

    if inverse:
        if value >= thresholds[0]:
            return 12.5
        elif value >= thresholds[1]:
            return 37.5
        elif value >= thresholds[2]:
            return 62.5
        else:
            return 87.5
    else:
        if value <= thresholds[0]:
            return 12.5
        elif value <= thresholds[1]:
            return 37.5
        elif value <= thresholds[2]:
            return 62.5
        else:
            return 87.5


def gate_score(gate_status):
    """Score gate status on 0-100 scale."""
    if not gate_status or gate_status == "None":
        return 0
    elif gate_status == "Partial":
        return 55
    elif gate_status == "Full":
        return 87.5
    elif gate_status == "Closed":
        return 100
    return 25


def avg_non_null(values):
    """Average of non-null values. Returns None if all null."""
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def compute_composite_score(fund):
    """Compute the full 6-factor composite score for a fund."""

    # ─── Factor 1: Redemption Pressure (25%) ───
    redemption_scores = [
        score_metric(fund.get("redemption_pct_nav"), SCORING_THRESHOLDS["redemption_rate"]),
        gate_score(fund.get("gate_status")),
        score_metric(fund.get("unmet_queue_pct", 0), SCORING_THRESHOLDS["unmet_queue"]),
    ]
    redemption = avg_non_null(redemption_scores)

    # ─── Factor 2: Leverage & Headroom (20%) ───
    leverage_scores = [
        score_metric(fund.get("de_ratio"), SCORING_THRESHOLDS["de_ratio"]),
        score_metric(fund.get("headroom_pct"), SCORING_THRESHOLDS["headroom"], inverse=True),
        score_metric(fund.get("debt_maturity_12mo_pct"), SCORING_THRESHOLDS["debt_maturity_12mo"]),
    ]
    leverage = avg_non_null(leverage_scores)

    # ─── Factor 3: Credit Quality (20%) ───
    credit_scores = [
        score_metric(fund.get("non_accrual_pct"), SCORING_THRESHOLDS["non_accrual"]),
        score_metric(fund.get("pik_income_pct"), SCORING_THRESHOLDS["pik_income"]),
        score_metric(fund.get("qoq_na_change"), SCORING_THRESHOLDS["qoq_na_change"]),
    ]
    credit = avg_non_null(credit_scores)

    # ─── Factor 4: Liquidity Adequacy (15%) ───
    # We don't have a direct liq/AUM from structured data, so we use
    # facility utilization as a proxy
    liquidity_scores = [
        score_metric(fund.get("facility_utilization"), SCORING_THRESHOLDS["facility_utilization"]),
    ]
    # If we have headroom, lower headroom implies lower liquidity
    if fund.get("headroom_pct") is not None:
        liq_from_headroom = score_metric(
            fund["headroom_pct"], [15, 10, 5], inverse=True
        )
        liquidity_scores.append(liq_from_headroom)

    liquidity = avg_non_null(liquidity_scores)

    # ─── Factor 5: Concentration Risk (10%) ───
    concentration_scores = [
        score_metric(fund.get("sw_tech_pct"), SCORING_THRESHOLDS["sw_tech_exposure"]),
        score_metric(fund.get("top_10_pct"), SCORING_THRESHOLDS["top_10_holdings"]),
    ]
    concentration = avg_non_null(concentration_scores)

    # ─── Factor 6: Governance (10%) ───
    # Without manual assessment, we default to moderate score
    # This can be overridden via manual_overrides.json
    governance = fund.get("governance_score", 37.5)

    # ─── Composite ───
    factors = {
        "redemption": redemption,
        "leverage": leverage,
        "credit": credit,
        "liquidity": liquidity,
        "concentration": concentration,
        "governance": governance,
    }

    # Compute weighted composite, handling None factors
    total_weight = 0
    weighted_sum = 0
    factor_details = {}

    for factor_name, factor_score in factors.items():
        weight = SCORING_WEIGHTS[factor_name]
        if factor_score is not None:
            weighted_sum += weight * factor_score
            total_weight += weight
            factor_details[factor_name] = round(factor_score, 1)
        else:
            factor_details[factor_name] = None

    if total_weight > 0:
        # Normalize to account for missing factors
        composite = (weighted_sum / total_weight) * 1.0
        composite = round(composite, 1)
    else:
        composite = None

    return composite, factor_details


def assign_rating(score):
    """Assign a risk rating based on composite score."""
    if score is None:
        return "unrated", "Unrated"

    for low, high, key, label in RATING_SCALE:
        if low <= score <= high:
            return key, label

    return "unrated", "Unrated"


def load_manual_overrides():
    """Load manual override data if available."""
    override_path = DATA_DIR / "manual_overrides.json"
    if override_path.exists():
        with open(override_path) as f:
            return json.load(f)
    return {}


def run_scoring():
    """Score all funds and produce final output."""
    input_path = DATA_DIR / "bdc_structured.json"
    if not input_path.exists():
        print("✗ bdc_structured.json not found. Run fetch_bdc_data.py first.")
        return

    with open(input_path) as f:
        data = json.load(f)

    overrides = load_manual_overrides()

    print(f"\nScoring {len(data['funds'])} funds...")

    scored_funds = []
    rating_counts = {"critical": 0, "high": 0, "elevated": 0, "moderate": 0, "low": 0, "unrated": 0}

    for fund in data["funds"]:
        ticker = fund.get("ticker", "")

        # Apply manual overrides if any
        if ticker in overrides:
            for key, value in overrides[ticker].items():
                fund[key] = value
                print(f"  {ticker}: manual override applied for {key}")

        # Compute composite score
        composite, factor_details = compute_composite_score(fund)
        rating_key, rating_label = assign_rating(composite)

        # Build final fund record for dashboard
        scored_fund = {
            # Identity
            "id": fund.get("cik", ticker.lower()),
            "ticker": ticker,
            "name": fund.get("name", ""),
            "type": fund.get("type", "Traded BDC"),

            # Scale metrics
            "aum": fund.get("aum_billions"),

            # Credit Quality
            "nonAccrual": fund.get("non_accrual_pct"),
            "pik": fund.get("pik_income_pct"),
            "swTech": fund.get("sw_tech_pct"),
            "qoqNaChange": fund.get("qoq_na_change"),

            # Leverage & Liquidity
            "deRatio": fund.get("de_ratio"),
            "headroom": fund.get("headroom_pct"),
            "facilityUtil": fund.get("facility_utilization"),
            "debtMat12": fund.get("debt_maturity_12mo_pct"),

            # Concentration
            "top10": fund.get("top_10_pct"),

            # Redemption
            "redmpPct": fund.get("redemption_pct_nav", 0),
            "gate": fund.get("gate_status", "None"),

            # Market
            "pNav": fund.get("price_nav_ratio"),
            "navPerShare": fund.get("nav_per_share"),
            "marketPrice": fund.get("market_price"),
            "marketCap": fund.get("market_cap_millions"),
            "dividendYield": fund.get("dividend_yield"),
            "ytdReturn": fund.get("ytd_return"),

            # Scoring
            "compositeScore": composite,
            "rating": rating_key,
            "ratingLabel": rating_label,
            "factorScores": factor_details,

            # Metadata
            "source": "F" if fund.get("latest_filing_form") else "E",
            "lastFiling": fund.get("latest_filing_date"),
            "filingPeriod": fund.get("latest_filing_period"),
            "numHoldings": fund.get("num_holdings", 0),
            "extractionNotes": fund.get("extraction_notes", ""),
            "lastUpdated": fund.get("last_updated"),
        }

        scored_funds.append(scored_fund)
        rating_counts[rating_key] = rating_counts.get(rating_key, 0) + 1

        score_str = f"{composite:.1f}" if composite else "N/A"
        print(f"  {ticker}: {score_str} ({rating_label})")

    # Sort by composite score descending (riskiest first), None last
    scored_funds.sort(
        key=lambda f: f["compositeScore"] if f["compositeScore"] is not None else -1,
        reverse=True,
    )

    # Build final output
    total_aum = sum(f["aum"] for f in scored_funds if f["aum"])

    output = {
        "generated": datetime.now().isoformat(),
        "fundCount": len(scored_funds),
        "totalAum": round(total_aum, 1),
        "ratingCounts": rating_counts,
        "source": data.get("source", "SEC EDGAR"),
        "marketDataUpdated": data.get("market_data_updated"),
        "textExtractionUpdated": data.get("text_extraction_updated"),
        "funds": scored_funds,
    }

    output_path = DATA_DIR / "funds.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'=' * 50}")
    print(f"✓ Scored {len(scored_funds)} funds → data/funds.json")
    print(f"  Total AUM: ~${total_aum:.0f}B")
    print(f"  Ratings: {rating_counts}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    run_scoring()
