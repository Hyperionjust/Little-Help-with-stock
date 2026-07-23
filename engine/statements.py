"""三表历史标准化：raw financials → 契约 statements 块（analysis.schema.json §statements）。

职责（方案阶段1-3）：
  把 fetch_data 合并后的扁平年报记录，整理成契约规定的 IS/BS/CF 三表形态，
  并产出诚实的 coverage——缺哪些行项、几年年报、几期季报、混源期间、
  以及"是否足以支撑三表建模+DCF"（sufficient_for_l2）。

设计红线：
  - **缺数就是缺数**：missing_line_items 如实列出，绝不补零。model_builder
    读到关键缺项时会拒绝建模并降级 L1——降级的依据就来自这里。
  - 单位统一为"百万"（engine 内部口径，见 _util.INTERNAL_UNIT），
    原始单位的换算已在 provider 层通过 normalize_unit 完成。

用法：
  from statements import build_statements
  analysis["statements"] = build_statements(raw)
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402  路径引导，见 engine/_paths.py
from _util import _denan, INTERNAL_UNIT, now_iso as _now_iso  # noqa: E402

# ── 扁平字段 → 三表归属（契约 statement_period 的三个桶）────────────
# 键 = raw financials 的标准化字段名（providers/base.FINANCIAL_FIELDS 及其超集），
# 值 = (报表, 契约行项名)。未在此登记的字段不会进 statements（但仍留在 raw 里）。
FIELD_MAP = {
    # 利润表
    "revenue":            ("income_statement", "revenue"),
    "cogs":               ("income_statement", "cogs"),
    "gross_profit":       ("income_statement", "gross_profit"),
    "rd_expense":         ("income_statement", "rd_expense"),
    "sga_expense":        ("income_statement", "sga_expense"),
    "operating_income":   ("income_statement", "operating_income"),
    "interest_expense":   ("income_statement", "interest_expense"),
    "interest_income":    ("income_statement", "interest_income"),
    "pretax_income":      ("income_statement", "pretax_income"),
    "tax":                ("income_statement", "tax"),
    "net_income":         ("income_statement", "net_income"),
    "minority_income":    ("income_statement", "minority_income"),
    "eps_basic":          ("income_statement", "eps_basic"),
    "eps_diluted":        ("income_statement", "eps_diluted"),
    "shares_basic":       ("income_statement", "shares_basic"),
    "shares_diluted":     ("income_statement", "shares_diluted"),
    # 资产负债表
    "cash":               ("balance_sheet", "cash"),
    "short_term_investments": ("balance_sheet", "short_term_investments"),
    "accounts_receivable": ("balance_sheet", "accounts_receivable"),
    "inventory":          ("balance_sheet", "inventory"),
    "current_assets":     ("balance_sheet", "current_assets"),
    "ppe_net":            ("balance_sheet", "ppe_net"),
    "goodwill":           ("balance_sheet", "goodwill"),
    "intangibles":        ("balance_sheet", "intangibles"),
    "total_assets":       ("balance_sheet", "total_assets"),
    "accounts_payable":   ("balance_sheet", "accounts_payable"),
    "short_term_debt":    ("balance_sheet", "short_term_debt"),
    "current_liabilities": ("balance_sheet", "current_liabilities"),
    "long_term_debt":     ("balance_sheet", "long_term_debt"),
    "total_debt":         ("balance_sheet", "total_debt"),
    "total_liabilities":  ("balance_sheet", "total_liabilities"),
    "minority_interest":  ("balance_sheet", "minority_interest"),
    "equity":             ("balance_sheet", "equity"),
    # 现金流量表
    "depreciation":       ("cash_flow", "depreciation_amortization"),
    "stock_based_comp":   ("cash_flow", "stock_based_comp"),
    "change_in_wc":       ("cash_flow", "change_in_wc"),
    "ocf":                ("cash_flow", "ocf"),
    "capex":              ("cash_flow", "capex"),
    "icf":                ("cash_flow", "icf"),
    "dividends_paid":     ("cash_flow", "dividends_paid"),
    "buybacks":           ("cash_flow", "buybacks"),
    "fcf":                ("cash_flow", "fcf"),
    # net_income 同时进 CF（间接法起点）——由 _derive 补，不重复映射
}

# L2（三表建模+DCF）的最低数据要求：这些行项在最近一期必须齐
L2_REQUIRED = [
    "revenue", "net_income", "total_assets", "total_liabilities", "equity",
    "cash", "ocf", "operating_income",
]
L2_MIN_YEARS = 3

# 全量关注行项（用于 missing_line_items 的诚实清点）
WATCHED = list(FIELD_MAP.keys())


def _derive(stmt_flat, buckets):
    """低风险派生：仅做恒等式内的推导，绝不无中生有。

    - gross_profit = revenue - cogs（两者都有且毛利缺失时）
    - fcf = ocf + capex（capex 惯例为负；两者都有且 fcf 缺失时）
    - cash_flow.net_income = 利润表 net_income（间接法起点）
    每笔派生都在 _derived 里留痕——派生值也是值，但读者有权知道它不是直接披露。
    """
    derived = []
    is_, cf = buckets["income_statement"], buckets["cash_flow"]
    if is_.get("gross_profit") is None and None not in (is_.get("revenue"), is_.get("cogs")):
        is_["gross_profit"] = is_["revenue"] - is_["cogs"]
        derived.append("gross_profit=revenue-cogs")
    if cf.get("fcf") is None and None not in (cf.get("ocf"), cf.get("capex")):
        cf["fcf"] = cf["ocf"] + cf["capex"]
        derived.append("fcf=ocf+capex")
    if cf.get("net_income") is None and is_.get("net_income") is not None:
        cf["net_income"] = is_["net_income"]
        derived.append("cf.net_income=is.net_income")
    return derived


def _one_period(stmt_flat, resolution, fin_meta, snapshot_date):
    per = {
        "period": stmt_flat.get("period", "unknown"),
        "_meta": {
            "source": stmt_flat.get("_field_sources") and
                      "+".join(sorted(set(stmt_flat["_field_sources"].values())))
                      or fin_meta.get("primary_source") or fin_meta.get("source", "unknown"),
            # live 数据带 report_date（akshare），fixture 带 _snapshot_date，都没有则用当前时刻。
            # 修复记录：初版只认 as_of/_snapshot_date，live 路径产出 as_of=None 被闸1拦下——
            # 这正是契约的价值：fixture 冒烟发现不了的 live/fixture 路径分叉，当场暴露。
            "as_of": (stmt_flat.get("as_of") or stmt_flat.get("report_date")
                      or snapshot_date or _now_iso()),
            "accounting_standard": fin_meta.get("accounting_standard"),
            "currency": resolution.get("currency") or "unknown",
            "unit": "million",  # engine 内部统一口径（INTERNAL_UNIT=百万）
        },
        "income_statement": {},
        "balance_sheet": {},
        "cash_flow": {},
    }
    for raw_key, (bucket, line) in FIELD_MAP.items():
        v = _denan(stmt_flat.get(raw_key))
        if v is not None:
            per[bucket][line] = v
    derived = _derive(stmt_flat, per)
    if derived:
        per["_meta"]["derived"] = derived
    if stmt_flat.get("_field_sources"):
        per["_meta"]["field_sources"] = stmt_flat["_field_sources"]
    return per


def build_statements(raw):
    """raw（fetch_data/import_local/fixture 的产物）→ 契约 statements 块。"""
    fin = raw.get("financials") or {}
    resolution = raw.get("resolution") or {}
    snapshot = raw.get("_snapshot_date")

    annual = [_one_period(s, resolution, fin, snapshot)
              for s in (fin.get("annual") or []) if s.get("period")]
    quarterly = [_one_period(s, resolution, fin, snapshot)
                 for s in (fin.get("quarterly") or []) if s.get("period")]

    # coverage：以最近一期年报为基准诚实清点
    latest_flat = (fin.get("annual") or [{}])[0]
    missing = [f for f in WATCHED if _denan(latest_flat.get(f)) is None]
    # 派生可补的不算缺（gross_profit / fcf 若已被派生出来）
    if annual:
        latest = annual[0]
        for name, bucket in [("gross_profit", "income_statement"), ("fcf", "cash_flow")]:
            if name in missing and latest[bucket].get(
                    "depreciation_amortization" if name == "depreciation" else name) is not None:
                missing.remove(name)

    l2_missing = [f for f in L2_REQUIRED if _denan(latest_flat.get(f)) is None]
    sufficient = len(annual) >= L2_MIN_YEARS and not l2_missing

    coverage = {
        "annual_years": len(annual),
        "quarterly_periods": len(quarterly),
        "missing_line_items": missing,
        "sufficient_for_l2": sufficient,
    }
    if not sufficient:
        coverage["l2_blockers"] = (
            ([f"缺关键行项: {', '.join(l2_missing)}"] if l2_missing else [])
            + ([f"年报仅 {len(annual)} 年 (<{L2_MIN_YEARS})"] if len(annual) < L2_MIN_YEARS else [])
        )
    if fin.get("mixed_source_periods"):
        coverage["mixed_source_periods"] = fin["mixed_source_periods"]

    return {"annual": annual, "quarterly": quarterly, "coverage": coverage}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="三表标准化（调试入口）")
    ap.add_argument("raw_json")
    args = ap.parse_args()
    raw = json.load(open(args.raw_json, encoding="utf-8"))
    st = build_statements(raw)
    cov = st["coverage"]
    print(json.dumps(cov, ensure_ascii=False, indent=2))
    print(f"annual={cov['annual_years']}y quarterly={cov['quarterly_periods']}q "
          f"L2就绪={cov['sufficient_for_l2']}")
