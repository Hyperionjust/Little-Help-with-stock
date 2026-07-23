"""单位归一化 + 量纲哨兵 + 门禁修复的单元测试。

对应方案第七部分缺陷 #1（市值单位不一致，P0）、#11（pe_pb_outlier 死代码）、
#28（PE 年报代理）、#29（ROIC 静默默认税率）。

这些正是"有单测就能抓到"的那类缺陷——原版没有单测，它们潜伏了整个生命周期。
"""
from __future__ import annotations
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from _util import normalize_unit, UNIT_FACTORS  # noqa: E402
import quality_gate as QG  # noqa: E402


# ── normalize_unit：单一换算权威 ─────────────────────────────────────

@pytest.mark.parametrize("value,from_unit,expect", [
    (1.947e12, "元", 1947000.0),     # 东财/efinance 的元 → 百万
    (19470, "亿元", 1947000.0),      # 腾讯的亿 → 百万
    (194700000, "万元", 1947000.0),  # tushare 的万元 → 百万
    (1947000, "百万", 1947000.0),    # 恒等
    (1.947, "十亿", 1947.0),
])
def test_normalize_unit_conversions(value, from_unit, expect):
    assert normalize_unit(value, from_unit) == pytest.approx(expect)


def test_normalize_unit_none_passthrough():
    assert normalize_unit(None, "元") is None


def test_normalize_unit_nan_becomes_none():
    assert normalize_unit(float("nan"), "元") is None


def test_normalize_unit_rejects_unknown_unit():
    """未知单位必须抛错。静默猜测比报错危险得多——这正是原 bug 的根源。"""
    with pytest.raises(ValueError):
        normalize_unit(1.0, "桶")


def test_unit_registry_covers_all_provider_conventions():
    """五个 provider 用到的全部单位别名必须已登记。"""
    for u in ["元", "亿元", "万元", "百万", "million"]:
        assert u in UNIT_FACTORS or u.lower() in UNIT_FACTORS


# ── 量纲哨兵（C6）：provider 又写错单位时的最后防线 ──────────────────

def _mini(quote):
    """最小可门禁的 analysis 骨架。"""
    return {
        "quote": quote,
        "valuation": {"pe_ttm": {"value": 30.0, "as_of": "2099-01-01"}},
        "meta": {"adjust_mode": "qfq"},
        "profitability": {}, "solvency": {}, "growth": {},
    }


def _av(v):
    return {"value": v, "source": "t", "as_of": None, "formula": "t"}


def test_sentinel_triggers_on_yuan_scale_market_cap():
    """复现真实事故场景：akshare 赢得 market_cap 字段且单位是元。

    茅台：市值 1.947e12 元被当作"百万"喂入 → 隐含股本 1.256e9 百万股，
    与申报流通盘 1256 百万股偏离 1e6 倍 → 必须 critical。
    """
    a = _mini({"price": _av(1550.0), "market_cap": _av(1.947e12),
               "float_shares": _av(1256.0)})
    QG.run_gate(a, today="2099-06-01")
    checks = [c["check"] for c in a["quality_report"]["critical"]]
    assert "unit_dimension_mismatch" in checks


def test_sentinel_silent_on_correct_units():
    """正确口径（百万）绝不误报。"""
    a = _mini({"price": _av(1550.0), "market_cap": _av(1947000.0),
               "float_shares": _av(1256.0)})
    QG.run_gate(a, today="2099-06-01")
    checks = [c["check"] for c in a["quality_report"]["critical"]]
    assert "unit_dimension_mismatch" not in checks


def test_sentinel_tolerates_small_float():
    """流通盘只占总股本 2% 的极端情形（50 倍差）不应误报——带宽是 1000 倍。"""
    a = _mini({"price": _av(10.0), "market_cap": _av(500000.0),   # 隐含 5e4 百万股
               "float_shares": _av(1000.0)})                       # 50 倍
    QG.run_gate(a, today="2099-06-01")
    checks = [c["check"] for c in a["quality_report"]["critical"]]
    assert "unit_dimension_mismatch" not in checks


def test_sentinel_absolute_bound_without_declared_shares():
    """无申报股本时退化为绝对哨兵：隐含股本 >1e13 股即 critical。"""
    a = _mini({"price": _av(100.0), "market_cap": _av(3.5e12)})    # 隐含 3.5e10 百万股
    QG.run_gate(a, today="2099-06-01")
    checks = [c["check"] for c in a["quality_report"]["critical"]]
    assert "unit_dimension_mismatch" in checks


# ── W2 修复：绝对界哨兵必须真的会响（原版是死代码） ──────────────────

def test_pe_outlier_fires_on_absurd_value():
    a = _mini({"price": _av(10.0), "market_cap": _av(1000.0), "float_shares": _av(100.0)})
    a["valuation"]["pe_ttm"] = {"value": 99999.0, "as_of": "2099-01-01"}
    QG.run_gate(a, today="2099-06-01")
    checks = [w["check"] for w in a["quality_report"]["warning"]]
    assert "pe_pb_outlier" in checks


def test_pe_outlier_silent_on_normal_value():
    a = _mini({"price": _av(10.0), "market_cap": _av(1000.0), "float_shares": _av(100.0)})
    QG.run_gate(a, today="2099-06-01")
    checks = [w["check"] for w in a["quality_report"]["warning"]]
    assert "pe_pb_outlier" not in checks


# ── W6：PE 年报代理口径必须显式提醒（缺陷 #28） ──────────────────────

def test_annual_proxy_pe_warns():
    a = _mini({"price": _av(10.0), "market_cap": _av(1000.0), "float_shares": _av(100.0)})
    a["valuation"]["pe_ttm"] = {"value": 30.0, "as_of": "2099-01-01",
                                "period": "2098FY (annual proxy)"}
    QG.run_gate(a, today="2099-06-01")
    checks = [w["check"] for w in a["quality_report"]["warning"]]
    assert "pe_ttm_is_annual_proxy" in checks


# ── #29：ROIC 默认税率必须披露进公式 ─────────────────────────────────

def test_roic_discloses_default_tax_rate():
    import fundamentals as F
    raw = {
        "quote": {"price": 10.0, "market_cap": 1000.0},
        "financials": {"source": "test", "annual": [
            {"period": "2098FY", "revenue": 100.0, "net_income": 20.0,
             "operating_income": 25.0, "total_debt": 10.0, "equity": 90.0,
             "total_assets": 150.0, "ocf": 22.0},
            {"period": "2097FY", "revenue": 90.0, "net_income": 18.0,
             "operating_income": 22.0, "total_debt": 10.0, "equity": 80.0,
             "total_assets": 140.0, "ocf": 20.0},
        ]},
    }
    out = F.compute_profitability(raw)
    roic = out.get("roic", {})
    assert roic.get("value") is not None
    # 数据未提供 effective_tax_rate → 公式必须写明用了默认 0.25
    assert "默认0.25" in roic.get("formula", ""), roic.get("formula")
