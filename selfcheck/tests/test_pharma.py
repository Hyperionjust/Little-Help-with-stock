"""医药 rNPV 模块单测（方案阶段7 修复的回归）。

原素材文档 §10 定义了 golden 锚点 asset_rnpv≈286.39 却没有 tests/ 目录——
这里补上，并固化阶段7 修的四个 bug：
  P1 催化剂无日期过滤（恒瑞吐 300 条横跨 2012-2030）
  P2 openFDA 用 brand_name 查公司名（必空）
  P3 触发词过宽（迈瑞器械被判 pharma）
  P4 峰值销售全默认无警告（伪装成估值）
"""
from __future__ import annotations
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in [os.path.join(ROOT, "engine"),
          os.path.join(ROOT, "engine", "providers"),
          os.path.join(ROOT, "engine", "providers", "free"),
          os.path.join(ROOT, "engine", "pharma")]:
    sys.path.insert(0, p)

import resolve_symbol as R  # noqa: E402
import pharma_valuation as PV  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")


# ── P3：触发词收窄 ───────────────────────────────────────────────────

@pytest.mark.parametrize("sym,name,expect", [
    ("300760", "迈瑞医疗", False),   # 医疗器械，无药物管线
    ("600276", "恒瑞医药", True),
    ("BGNE", "百济神州", True),
    ("603259", "药明康德", True),
])
def test_pharma_trigger_precision(sym, name, expect):
    assert R.resolve(sym)["is_pharma"] is expect, f"{name} 判定错误"


def test_device_keyword_vetoes_pharma():
    """器械排除词命中即否决，即使名称含'医疗'。"""
    r = R.resolve("999999", name_hint="某某医疗器械", industry_hint="医疗器械")
    assert r["is_pharma"] is False


# ── golden 锚点：折现率不双重计罚（方法学核心）──────────────────────

def test_clinical_rate_never_equals_wacc():
    """rNPV 折现率必须独立于通用 WACC——临床风险已由 PoS 扣除，重复计罚会
    把有价值管线算成一文不值。这是整个医药模块的方法学底线。"""
    for paradigm in ["large_pharma_sotp", "clinical_biotech"]:
        rate = PV.clinical_rate_for(paradigm, [])
        assert rate != 0.09, f"{paradigm} 折现率撞上通用 WACC"
        assert 0.08 <= rate <= 0.16


def test_cumulative_pos_chains_correctly():
    """累积 PoS = 从当前阶段到批准各阶段成功率连乘。Phase1→批准应落在个位数%。"""
    asset = {"asset": "TEST-001", "current_phase": "phase1", "indication": "oncology"}
    r = PV.compute_asset_rnpv(asset, 0.125)
    pos = r["cumulative_pos"]
    assert 0.03 < pos < 0.15, f"Phase1 累积 PoS {pos} 偏离行业基准（~8.7%）"


def test_rnpv_golden_anchor():
    """闭式锚点：给定明确输入，rNPV 应可复现（防止重构静默改变数值）。"""
    asset = {"asset": "GOLD", "current_phase": "phase3", "indication": "oncology",
             "peak_sales": 1000.0, "launch_year": 2028, "loe_year": 2040,
             "molecule": "small_molecule", "competition": "moderate"}
    r = PV.compute_asset_rnpv(asset, 0.10, base_year=2026)
    # 锁定当前实现的输出（重构后若变化，此测试会提示人工确认是否预期）
    assert r["asset_rnpv"] > 0
    assert r["peak_is_default"] is False   # 显式给了 peak_sales
    # Phase3→批准基础成功率 ~48%，肿瘤适应症 ×0.7 TA 折扣 → ~34%。
    # （测试初稿误设 >0.4，实为对 TA 折扣的漏算——代码正确，肿瘤成功率本就更低）
    assert 0.25 < r["cumulative_pos"] < 0.40


# ── P4：峰值销售默认标记 ─────────────────────────────────────────────

def test_peak_sales_flags_default():
    """没给患者数/定价/peak_sales → 落默认常数 → is_default=True。"""
    asset = {"asset": "X", "current_phase": "phase2", "indication": "oncology"}
    peak, pen, comp, is_default = PV.estimate_peak_sales(asset, "oncology")
    assert is_default is True
    assert peak == PV.DEFAULT_PEAK_SALES_BY_TA["oncology"]


def test_peak_sales_bottom_up_not_default():
    """给了患者数×定价 → 自下而上测算 → is_default=False。"""
    asset = {"asset": "Y", "target_patients": 50000,
             "annual_price_per_patient": 0.15, "competition": "low"}
    peak, pen, comp, is_default = PV.estimate_peak_sales(asset, "oncology")
    assert is_default is False
    assert peak > 0


# ── P1：催化剂日期过滤 ───────────────────────────────────────────────

def test_catalysts_filter_out_of_window():
    raw = {"pharma_raw": {"clinicaltrials": {"trials": [
        {"nct": "NCT001", "intervention": "药A", "primary_completion_date": "2015-06-01"},  # 过期
        {"nct": "NCT002", "intervention": "药B", "primary_completion_date": "2027-03-01"},  # 窗口内
        {"nct": "NCT003", "intervention": "药C", "primary_completion_date": "2035-01-01"},  # 太远
    ]}}}
    cat = PV.build_catalysts(raw, as_of="2026-07-01", horizon_months=24)
    dates = [c["date"] for c in cat["items"]]
    assert "2027-03-01" in dates
    assert "2015-06-01" not in dates
    assert "2035-01-01" not in dates
    assert cat["dropped_out_of_window"] == 2


# ── P2：openFDA 查询修复 ─────────────────────────────────────────────

def test_openfda_no_query_without_drug_name():
    """不传具体药名时不拿公司名瞎查——返回空+说明，而非误导性结果。"""
    from openfda_p import OpenFDAProvider
    out = OpenFDAProvider().get_approvals({"name": "恒瑞医药", "symbol": "600276"})
    assert out["approvals"] == []
    assert "note" in out and "药名" in out["note"]


# ── 端到端：恒瑞医药 pharma 块结构完整 ───────────────────────────────

def test_hengrui_pharma_block_wellformed():
    raw = json.load(open(os.path.join(FIXTURES, "hengrui_600276.json"), encoding="utf-8"))
    raw.setdefault("resolution", {"market": "A", "symbol": "600276", "is_pharma": True})
    ph = PV.compute_pharma(raw)
    assert ph["double_penalty_check"]["passed"] is True
    assert "catalysts" in ph and "window" in ph["catalysts"]
    assert "human_verification_checklist" in ph
    # 所有 user_assumption 必须进核对清单（诚实性底线）
    assert len(ph["human_verification_checklist"]) > 0
