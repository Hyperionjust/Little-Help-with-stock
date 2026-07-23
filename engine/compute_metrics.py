"""Orchestrator: raw_data.json -> analysis JSON (通用部分).

组合 fundamentals + technicals，填 meta/resolution/quote，跨源价格校验。医药段由 pharma_valuation.py
另行追加。用法：python compute_metrics.py raw_data.json [-o analysis.json]
"""
from __future__ import annotations
import argparse
import os
import sys

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402  路径引导，见 engine/_paths.py
from _util import av, now_iso, load_json, dump_json, safe_div  # noqa: E402
import fundamentals as F  # noqa: E402
import technicals as T  # noqa: E402

SCHEMA_VERSION = "1.1.0"  # 契约扩展：新增 statements/segments/peers/estimates/capital_structure/provenance/diagnostics
DISCLAIMER = "本报告仅为数据分析，不构成任何投资建议；数据可能有误差与滞后，据此决策风险自负。"


def _cross_source_price(raw):
    """跨源收盘价校验：多个 provider 都给了收盘价则算相对差。"""
    xs = raw.get("quote", {}).get("cross_source", {}).get("prices", {})
    xs = {k: v for k, v in xs.items() if v is not None}
    if len(xs) < 2:
        return {"sources": xs, "max_rel_diff": None, "passed": None}
    vals = list(xs.values())
    lo, hi = min(vals), max(vals)
    rel = safe_div(hi - lo, lo)
    return {"sources": xs, "max_rel_diff": rel, "passed": (rel is not None and rel <= 0.01)}


def build_quote(raw):
    q = raw.get("quote", {})
    def wrap(field, formula, unit=None):
        v = q.get(field)
        if isinstance(v, dict):
            return v
        return av(v, raw.get("field_sources", {}).get(field, q.get("source", "unknown")),
                  formula, as_of=q.get("as_of"), unit=unit)
    out = {
        "price": wrap("price", "最新成交价", unit=raw.get("resolution", {}).get("currency")),
        "market_cap": wrap("market_cap", "price×总股本 或 源提供市值",
                           unit=raw.get("resolution", {}).get("currency")),
        "prev_close": wrap("prev_close", "上一交易日收盘"),
        "cross_source_check": _cross_source_price(raw),
    }
    return out


def _raw_inputs(raw):
    """把 TTM 关键原始量落到 meta，供 render_xlsx 建活公式 & verify_consistency 对账。"""
    fin = raw.get("financials", {})
    ttm = fin.get("ttm") or (fin.get("annual", [{}])[0] if fin.get("annual") else {})
    gp = ttm.get("gross_profit")
    if gp is None and ttm.get("revenue") is not None and ttm.get("cogs") is not None:
        gp = ttm["revenue"] - ttm["cogs"]
    return {
        "revenue_ttm": ttm.get("revenue"), "net_income_ttm": ttm.get("net_income"),
        "equity": ttm.get("equity"), "total_assets": ttm.get("total_assets"),
        "total_liabilities": ttm.get("total_liabilities"),
        "current_assets": ttm.get("current_assets"),
        "current_liabilities": ttm.get("current_liabilities"),
        "inventory": ttm.get("inventory"), "gross_profit": gp, "ocf": ttm.get("ocf"),
    }


def compute(raw):
    res = raw.get("resolution", {})
    analysis = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generated_at": now_iso(),
            "generator": "stock-metrics-pro/compute_metrics.py",
            "adjust_mode": raw.get("kline", {}).get("adjust", "unknown"),
            "accounting_standard": raw.get("financials", {}).get("accounting_standard"),
            "base_currency": res.get("currency"),
            "disclaimer": DISCLAIMER,
            "_raw_inputs": _raw_inputs(raw),
        },
        "resolution": {
            "market": res.get("market"),
            "symbol": res.get("symbol"),
            "name": res.get("name"),
            "currency": res.get("currency"),
            "exchange": res.get("exchange"),
            "industry_tag": res.get("industry_tag"),
            "is_pharma": bool(res.get("is_pharma", False)),
            "benchmark_index": res.get("benchmark_index"),
        },
        "quote": build_quote(raw),
        "valuation": F.compute_valuation(raw),
        "profitability": F.compute_profitability(raw),
        "reverse_validation": F.compute_reverse_validation(raw),
        "solvency": F.compute_solvency(raw),
        "growth": F.compute_growth(raw),
        "technicals": T.compute_technicals(raw),
        "data_gaps": raw.get("data_gaps", []),
        # quality_report 由 quality_gate.py 填充；先放占位以满足 schema required
        "quality_report": {"passed": None, "degraded": False, "critical": [], "warning": []},
    }
    return analysis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    raw = load_json(args.raw)
    analysis = compute(raw)
    out = args.out or f"{raw.get('resolution', {}).get('symbol', 'out')}_analysis.json"
    dump_json(analysis, out)
    print(out)


if __name__ == "__main__":
    main()
