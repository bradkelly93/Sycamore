"""Metric calculation library.

Every public function takes a FinancialsFrame and returns either a pandas
Series indexed by period (YYYY-MM-DD) or a single scalar. Bank-specific
metrics live in `bank.py`. All formulas are documented in the function
docstring so the output is auditable per CLAUDE.md.
"""

from .profitability import (
    revenue_fy,
    gross_profit_fy,
    gross_margin_fy,
    operating_margin_fy,
    net_income_fy,
    nopat_fy,
    invested_capital_fy,
    roic_fy,
    roe_fy,
    rotce_fy,
    gross_margin_stability,
)
from .balance_sheet import (
    total_debt_fy,
    net_debt_fy,
    ebitda_fy,
    net_debt_to_ebitda_fy,
    interest_coverage_fy,
    tangible_book_value_fy,
)
from .cashflow import (
    free_cash_flow_fy,
    fcf_margin_fy,
    fcf_conversion_fy,
)
from .valuation import (
    pe_fy,
    ev_ebitda_fy,
    fcf_yield_fy,
    p_tbv_fy,
    percentile_vs_history,
)
from .bank import (
    is_bank,
    net_interest_margin_fy,
    allowance_to_loans_fy,
)
from .volatility import (
    expected_move_pct,
    expected_move_price,
    sigma_down_price,
    put_skew,
    implied_downside_breaches_mos,
    iv_index,
    iv_rank_pct,
    iv_percentile_pct,
    expected_move_into_earnings,
    volatility_overlay,
)

__all__ = [
    "revenue_fy", "gross_profit_fy", "gross_margin_fy", "operating_margin_fy",
    "net_income_fy", "nopat_fy", "invested_capital_fy", "roic_fy", "roe_fy",
    "rotce_fy", "gross_margin_stability",
    "total_debt_fy", "net_debt_fy", "ebitda_fy", "net_debt_to_ebitda_fy",
    "interest_coverage_fy", "tangible_book_value_fy",
    "free_cash_flow_fy", "fcf_margin_fy", "fcf_conversion_fy",
    "pe_fy", "ev_ebitda_fy", "fcf_yield_fy", "p_tbv_fy",
    "percentile_vs_history",
    "is_bank", "net_interest_margin_fy", "allowance_to_loans_fy",
    "expected_move_pct", "expected_move_price", "sigma_down_price", "put_skew",
    "implied_downside_breaches_mos", "iv_index", "iv_rank_pct",
    "iv_percentile_pct", "expected_move_into_earnings", "volatility_overlay",
]
