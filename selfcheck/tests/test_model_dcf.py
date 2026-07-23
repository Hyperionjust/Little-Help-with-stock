"""三表引擎 + DCF 的单元测试（方案阶段4）。

两个真实快照（2026-07 live 数据）固化为 fixture：
  l2_moutai_snapshot     贵州茅台——成熟稳态标的，DCF 的"正常路径"
  l2_cambricon_snapshot  寒武纪——刚扭亏高增长标的，DCF 的"压力路径"

自检中修掉的四个 bug 全部固化为回归用例：
  B1 拐点标的中位利润率为负 → 全按亏损投影（EV 为负、格式化崩溃）
  B2 少数股东权益缺字段 → 恒等式差 9,321 百万（茅台习酒）
  B3 恒等式检查打舍入后序列 → 分位舍入累积成假失败
  B4 隐含增速触顶输出 1.5 → 误导性数字，应为 None + 说明
"""
from __future__ import annotations
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in [os.path.join(ROOT, "engine"), os.path.join(ROOT, "analyst"),
          os.path.join(ROOT, "contracts")]:
    sys.path.insert(0, p)

import model_builder as MB  # noqa: E402
import dcf as DCF  # noqa: E402
import validate as V  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")


def _load(name):
    return json.load(open(os.path.join(FIXTURES, name), encoding="utf-8"))


@pytest.fixture(scope="module")
def moutai_model():
    a = _load("l2_moutai_snapshot.json")
    return DCF.build_full_model(a, "l2_moutai_snapshot.json"), a


@pytest.fixture(scope="module")
def cambricon_model():
    a = _load("l2_cambricon_snapshot.json")
    return DCF.build_full_model(a, "l2_cambricon_snapshot.json"), a


# ── 茅台：正常路径 ────────────────────────────────────────────────────

def test_moutai_reaches_l2(moutai_model):
    m, _ = moutai_model
    assert m["valuation_level"] == "L2"
    assert m["dcf"]["value_per_share"] is not None


def test_moutai_passes_gate2(moutai_model):
    m, _ = moutai_model
    passed, failures = V.gate2(m)
    assert passed, failures


def test_moutai_dcf_within_sane_band_of_market(moutai_model):
    """成熟稳态标的的 DCF 应落在市价 ±40% 内——超出说明假设体系有系统性偏差。

    （这不是"DCF 必须等于市价"——是合理性哨兵：茅台这类被充分研究的
    大盘蓝筹，DCF 与市场共识大幅背离更可能是模型错了而不是市场错了。）
    """
    m, a = moutai_model
    price = a["quote"]["price"]["value"]
    vps = m["dcf"]["value_per_share"]
    assert 0.6 * price < vps < 1.4 * price, f"DCF {vps} vs 市价 {price}"


def test_moutai_tv_share_below_80pct(moutai_model):
    m, _ = moutai_model
    assert m["dcf"]["terminal"]["tv_pct_of_ev"] < 0.80


def test_moutai_flags_latest_growth_negative(moutai_model):
    """2025FY 营收微降（174,144→172,054 百万）——增长动能拐点必须被提示。"""
    m, _ = moutai_model
    checks = [w["check"] for w in m["gate"]["warning"]]
    assert "latest_growth_negative" in checks


def test_moutai_minority_in_equity_bridge(moutai_model):
    """B2 回归：习酒等少数股东权益（约 9,321 百万）必须进权益桥。"""
    m, _ = moutai_model
    assert m["dcf"]["equity_bridge"]["less_minority"] > 5000


def test_moutai_implied_growth_is_finite(moutai_model):
    """茅台市价温和，隐含增速应反推出具体数字（非触顶）。"""
    m, _ = moutai_model
    ci = m["scenarios"]["current_implied"]
    assert ci["implied_y1_growth"] is not None
    assert -0.2 < ci["implied_y1_growth"] < 0.3


def test_moutai_tornado_ranks_assumptions(moutai_model):
    """龙卷风：至少 3 条假设有量化的每股影响，且利润率应是最大驱动之一。"""
    m, _ = moutai_model
    impacts = {a["id"]: a.get("per_share_impact") for a in m["assumptions"]}
    quantified = [k for k, v in impacts.items() if v]
    assert len(quantified) >= 3
    top2 = sorted(impacts, key=lambda k: -(impacts[k] or 0))[:2]
    assert "op_margin" in top2 or "terminal_g" in top2


# ── 寒武纪：压力路径（工具的诚实性测试）──────────────────────────────

def test_cambricon_reaches_l2_with_full_warnings(cambricon_model):
    """压力标的不硬拒也不硬吹：给出 L2 但必须带全套诚实性警告。"""
    m, _ = cambricon_model
    assert m["valuation_level"] == "L2"
    checks = [w["check"] for w in m["gate"]["warning"]]
    for expected in ["tv_dominates_ev", "turnaround_single_year_margin",
                     "negative_historical_ufcf", "dcf_far_from_market"]:
        assert expected in checks, f"缺诚实性警告 {expected}"


def test_cambricon_passes_gate2(cambricon_model):
    """高警告 ≠ 不自洽。模型内部勾稽必须照样全过。"""
    m, _ = cambricon_model
    passed, failures = V.gate2(m)
    assert passed, failures


def test_cambricon_margin_uses_latest_year_not_median(cambricon_model):
    """B1 回归：2023/24 亏损 2025 扭亏——margin 必须取最近年（正值）而非中位（负值）。"""
    m, _ = cambricon_model
    margin = next(a for a in m["assumptions"] if a["id"] == "op_margin")
    assert margin["value"] > 0
    assert margin["source_type"] == "user_assumption"   # 单年历史=主观假设
    assert "拐点" in margin["basis"]


def test_cambricon_implied_growth_honestly_capped(cambricon_model):
    """B4 回归：150% 增速仍撑不起市价时，输出 None+说明，不输出误导性的 1.5。"""
    m, _ = cambricon_model
    ci = m["scenarios"]["current_implied"]
    assert ci["implied_y1_growth"] is None
    assert "150%" in ci.get("implied_note", "")


def test_cambricon_bear_infeasible_is_explained(cambricon_model):
    """bear 情景 DCF 无解不是空着——note 必须解释这本身是信息。"""
    m, _ = cambricon_model
    bear = m["scenarios"]["bear"]
    assert bear["target"] is None
    assert "note" in bear and "无解" in bear["note"]
    assert m["scenarios"].get("weighted_target_note")


def test_cambricon_dcf_far_below_market(cambricon_model):
    """数值合理性：寒武纪基本面 DCF 应远低于市价（市场定价的是远期叙事）。"""
    m, a = cambricon_model
    price = a["quote"]["price"]["value"]
    vps = m["dcf"]["value_per_share"]
    assert vps < price * 0.1    # DCF < 市价的 10%——这正是该标的的真实画像


# ── 合成用例：修掉的机制性 bug ────────────────────────────────────────

def _synthetic_analysis(rows, price=100.0, mcap=100000.0):
    annual = []
    for r in reversed(rows):
        annual.append({
            "period": r["period"],
            "_meta": {"source": "t", "as_of": "2099-01-01", "currency": "CNY", "unit": "million"},
            "income_statement": {"revenue": r["rev"], "operating_income": r["op"],
                                 "net_income": r["ni"]},
            "balance_sheet": {"total_assets": r["assets"], "total_liabilities": r["liab"],
                              "equity": r["eq"], "cash": r["cash"],
                              "total_debt": r.get("debt", 0.0)},
            "cash_flow": {"ocf": r.get("ocf", r["ni"])},
        })
    return {"resolution": {"market": "A", "symbol": "TEST", "currency": "CNY"},
            "quote": {"price": {"value": price}, "market_cap": {"value": mcap}},
            "statements": {"annual": annual, "coverage": {}}}


def test_identity_holds_with_minority_residual():
    """B2/B3 回归：assets−liab−equity 有残差（少数股东）时恒等式仍机器精度成立。"""
    rows = [{"period": f"209{i}FY", "rev": 1000 + 50 * i, "op": 300, "ni": 220,
             "assets": 2000 + 100 * i, "liab": 500, "eq": 1400 + 100 * i,   # 残差=100
             "cash": 800, "debt": 0} for i in range(4)]
    a = _synthetic_analysis(rows)
    assumptions, proj, blockers = MB.build_model(a)
    assert not blockers, blockers
    c = proj["checks"]["balance_sheet_balanced"]
    assert c["passed"] and c["value"] < 1e-6


def test_turnaround_detection_on_synthetic():
    """B1 机制：中位 margin 为负 + 最近年为正 → 用最近年 + user_assumption。"""
    rows = [
        {"period": "2096FY", "rev": 100, "op": -40, "ni": -45, "assets": 500,
         "liab": 100, "eq": 400, "cash": 200, "debt": 0},
        {"period": "2097FY", "rev": 200, "op": -20, "ni": -25, "assets": 550,
         "liab": 100, "eq": 450, "cash": 180, "debt": 0},
        {"period": "2098FY", "rev": 600, "op": 180, "ni": 150, "assets": 800,
         "liab": 120, "eq": 680, "cash": 250, "debt": 0},
    ]
    a = _synthetic_analysis(rows)
    assumptions, proj, _ = MB.build_model(a)
    margin = next(x for x in assumptions if x["id"] == "op_margin")
    assert margin["value"] == pytest.approx(180 / 600)
    assert margin["source_type"] == "user_assumption"
    assert proj["_signals"]["turnaround"] is True


def test_negative_ev_degrades_honestly():
    """持续亏损无反转 → EV≤0 → 降级 L1 并说明，绝不产出 None 链。"""
    rows = [{"period": f"209{i}FY", "rev": 100 + 10 * i, "op": -50, "ni": -55,
             "assets": 500, "liab": 100, "eq": 400, "cash": 300, "debt": 0}
            for i in range(4)]
    a = _synthetic_analysis(rows)
    m = DCF.build_full_model(a, "x.json")
    assert m["valuation_level"] == "L1"
    assert "degraded_from_l2" in m and m["degraded_from_l2"]["reason"]


def test_sensitivity_center_equals_dcf(moutai_model):
    m, _ = moutai_model
    assert m["sensitivity"]["base_cell_equals_dcf"] is True
    r, c = m["sensitivity"]["base_cell"]
    center = m["sensitivity"]["matrix"][r][c]
    assert center == pytest.approx(m["dcf"]["value_per_share"], rel=0.005)


def test_mid_year_convention_always_on(moutai_model, cambricon_model):
    for mm in (moutai_model[0], cambricon_model[0]):
        assert mm["dcf"]["mid_year_convention"] is True


def test_equity_bridge_never_adds_excess_cash(moutai_model, cambricon_model):
    """原素材缺陷 #2 的永久回归：权益桥禁止出现 add_excess_cash。"""
    for mm in (moutai_model[0], cambricon_model[0]):
        assert "add_excess_cash" not in mm["dcf"]["equity_bridge"]
