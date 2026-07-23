"""Provider 抽象基类 + 字段级 fallback 引擎的数据契约。

设计意图：计算层只认「标准化字段」，不认数据源。新增付费源(Tushare Pro/iFind/Wind/Cortellis/Evaluate)
只需实现本基类的方法，不改计算层——这是抽象的全部价值。

每个方法返回 dict[field] = value（缺失填 None）。fallback 引擎按字段取第一个非 None，逐字段记录来源。
"""
from __future__ import annotations
from abc import ABC


# 标准化字段清单（计算层依赖这些键）
QUOTE_FIELDS = ["price", "market_cap", "prev_close", "float_shares", "total_shares"]
FINANCIAL_FIELDS = [
    "revenue", "cogs", "gross_profit", "operating_income", "net_income", "ocf",
    "total_assets", "total_liabilities", "equity", "current_assets", "current_liabilities",
    "inventory", "accounts_receivable", "cash", "total_debt", "interest_expense",
    "effective_tax_rate", "eps_diluted", "shares_diluted", "depreciation",
]


class Provider(ABC):
    """数据源抽象基类。子类实现能力子集；不支持的接口返回空/None，由 fallback 补。"""

    name = "base"
    supports_markets = ()  # ("A","HK","US")

    def get_quote(self, resolved) -> dict:
        """返回 {price, market_cap, prev_close, float_shares, total_shares}。缺失填 None。"""
        return {}

    def get_kline(self, resolved, adjust="qfq") -> dict:
        """返回 {adjust, source, as_of, dates, open, high, low, close, volume}。"""
        return {}

    def get_financials(self, resolved) -> dict:
        """返回 {source, accounting_standard, annual:[...], quarterly:[...]}。"""
        return {}

    def get_valuation(self, resolved) -> dict:
        """返回估值辅助字段（如源直接提供的 pe/pb/股息），可空。"""
        return {}

    # 医药可选接口（clinicaltrials/openfda provider 实现）
    def get_clinical(self, resolved) -> dict:
        return {}

    def get_approvals(self, resolved) -> dict:
        return {}
