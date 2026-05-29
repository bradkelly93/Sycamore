"""Bank-specific metrics.

A ticker is treated as a bank if its EDGAR companyfacts include the
`NetInterestIncome` concept with any data. That's a cleaner heuristic than
GICS sector mapping and matches what the brief asks for in the bank model.
"""

from __future__ import annotations

import pandas as pd

from ..adapters.base import FinancialsFrame
from .profitability import _series


def is_bank(ff: FinancialsFrame) -> bool:
    """True if the ticker reports bank-specific tags (NII or Deposits)."""
    if ff.df.empty:
        return False
    bank_tags = {"NetInterestIncome", "Deposits", "AllowanceForLoanAndLeaseLosses"}
    present = set(ff.df["concept"].unique())
    return bool(bank_tags & present)


def net_interest_margin_fy(ff: FinancialsFrame) -> pd.Series:
    """NIM = Net Interest Income / average earning assets.

    Proxy: use Assets as denominator (true earning-assets tag is inconsistent
    across filers). Tagged accordingly in the output xlsx.
    """
    nii = _series(ff, "NetInterestIncome")
    assets = _series(ff, "Assets")
    if nii.empty or assets.empty:
        return pd.Series(dtype=float, name="NIM_proxy")
    avg_assets = (assets + assets.shift(1)) / 2.0
    common = nii.index.intersection(avg_assets.index)
    return (nii.loc[common] / avg_assets.loc[common]).rename("NIM_proxy")


def allowance_to_loans_fy(ff: FinancialsFrame) -> pd.Series:
    """ACL / Loans — the reserve-coverage ratio."""
    acl = _series(ff, "AllowanceForLoanAndLeaseLosses")
    loans = _series(ff, "LoansAndLeasesReceivableNet")
    if acl.empty or loans.empty:
        return pd.Series(dtype=float, name="Allowance_to_Loans")
    common = acl.index.intersection(loans.index)
    return (acl.loc[common] / loans.loc[common]).rename("Allowance_to_Loans")
