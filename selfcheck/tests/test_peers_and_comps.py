"""可比公司圈定 + 调整框架的单元测试（方案阶段3）。

peers 需联网逐个取同业，故全部用依赖注入的假 fetch/compute 离线测——
测的是圈定逻辑、分位数计算、调整框架，不测网络。
"""
from __future__ import annotations
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "analyst"))

import peers as P  # noqa: E402
import comps as C  # noqa: E402


# ── 分位数：必须与 numpy 完全一致 ─────────────────────────────────────

def test_percentiles_match_numpy():
    np = pytest.importorskip("numpy")
    vals = [68.2, 52.1, 41.0, 33.2, 21.4, 45.5, 38.9]
    s = P._percentiles(vals)
    assert s["median"] == pytest.approx(float(np.percentile(vals, 50)))
    assert s["p75"] == pytest.approx(float(np.percentile(vals, 75)))
    assert s["p25"] == pytest.approx(float(np.percentile(vals, 25)))
    assert s["max"] == max(vals)
    assert s["min"] == min(vals)
    assert s["n"] == 7


def test_percentiles_single_value():
    s = P._percentiles([42.0])
    assert s["median"] == 42.0 and s["n"] == 1


def test_percentiles_ignores_none():
    s = P._percentiles([10.0, None, 20.0, None, 30.0])
    assert s["n"] == 3 and s["median"] == 20.0


def test_percentiles_empty_returns_none():
    assert P._percentiles([None, None]) is None


# ── 同业圈定：命中名录 / 降级 ─────────────────────────────────────────

def test_universe_matches_industry():
    codes, basis = P._match_universe("申万-食品饮料-白酒")
    assert "600519" in codes
    assert "命中" in basis


def test_universe_miss_degrades_honestly():
    codes, basis = P._match_universe("申万-某冷门行业")
    assert codes == []
    assert "手工指定" in basis


def test_no_industry_tag_degrades():
    codes, basis = P._match_universe(None)
    assert codes == []


# ── 依赖注入的离线取数 ────────────────────────────────────────────────

FAKE = {
    "600519": {"pe_ttm": 30.0, "pb": 9.0, "ps_ttm": 13.0, "ev_ebitda": 22.0,
               "market_cap": 1900000, "revenue_cagr_3y": 0.15, "net_margin": 0.52, "name": "贵州茅台"},
    "000858": {"pe_ttm": 22.0, "pb": 6.0, "ps_ttm": 7.0, "ev_ebitda": 16.0,
               "market_cap": 900000, "revenue_cagr_3y": 0.12, "net_margin": 0.35, "name": "五粮液"},
    "000568": {"pe_ttm": 20.0, "pb": 7.0, "ps_ttm": 6.5, "ev_ebitda": 15.0,
               "market_cap": 250000, "revenue_cagr_3y": 0.18, "net_margin": 0.33, "name": "泸州老窖"},
    "600809": {"pe_ttm": 24.0, "pb": 8.0, "ps_ttm": 9.0, "ev_ebitda": 18.0,
               "market_cap": 300000, "revenue_cagr_3y": 0.20, "net_margin": 0.36, "name": "山西汾酒"},
}


def _fake_deps():
    def resolve(code):
        return {"market": "A", "symbol": code.split(".")[0], "currency": "CNY"}

    def fetch(res):
        return {"_sym": res["symbol"]}

    def compute(raw):
        m = FAKE.get(raw["_sym"])
        if not m:
            raise KeyError(raw["_sym"])
        av = lambda v: {"value": v}
        return {"quote": {"market_cap": av(m["market_cap"])},
                "valuation": {k: av(m[k]) for k in ["pe_ttm", "pb", "ps_ttm", "ev_ebitda"]},
                "profitability": {"net_margin": av(m["net_margin"])},
                "growth": {"revenue_cagr_3y": av(m["revenue_cagr_3y"])}}
    return fetch, resolve, compute


@pytest.fixture
def peers_block():
    resolved = {"market": "A", "symbol": "600519", "currency": "CNY",
                "industry_tag": "申万-食品饮料-白酒"}
    fetch, resolve, compute = _fake_deps()
    return P.build_peers(resolved, target_multiples=FAKE["600519"],
                         fetch_fn=fetch, resolve_fn=resolve, compute_fn=compute), resolved


def test_build_peers_produces_valid_sample(peers_block):
    pk, _ = peers_block
    assert pk["available"] is True
    assert pk["stats"]["pe_ttm"]["n"] == 3  # 名录白酒6家，去重后取到的同业
    target = next(it for it in pk["items"] if it["is_target"])
    assert target["symbol"] == "600519"


def test_stats_exclude_target(peers_block):
    """分位数只用同业，绝不含目标自己——否则'目标 vs 同业'失去意义。"""
    pk, _ = peers_block
    peers_only = [it for it in pk["items"] if not it["is_target"]]
    assert pk["stats"]["pe_ttm"]["n"] == len(peers_only)
    assert 30.0 not in [pk["stats"]["pe_ttm"]["max"]]  # 目标 PE30 不在同业统计里


def test_manual_peers_override():
    resolved = {"market": "A", "symbol": "600519", "currency": "CNY",
                "industry_tag": "任意"}
    fetch, resolve, compute = _fake_deps()
    pk = P.build_peers(resolved, target_multiples=FAKE["600519"], manual="000858,000568",
                       fetch_fn=fetch, resolve_fn=resolve, compute_fn=compute)
    assert "手工指定" in pk["selection_basis"]
    syms = {it["symbol"].split(".")[0] for it in pk["items"]}
    assert {"000858", "000568"} <= syms


def test_uncircleable_degrades_not_crashes():
    resolved = {"market": "A", "symbol": "999999", "currency": "CNY",
                "industry_tag": "无法识别的行业"}
    pk = P.build_peers(resolved)
    assert pk["available"] is False
    assert "手工指定" in pk["_note"]


# ── comps 调整框架 ───────────────────────────────────────────────────

def test_comps_decomposes_premium(peers_block):
    """核心价值：把裸溢价拆成'多少由基本面解释'。"""
    pk, resolved = peers_block
    cp = C.build_comps(pk, resolved, target_forward_eps=70.0)
    assert cp["available"] is True
    assert cp["metric"] == "pe_ttm"          # 白酒主用 PE
    assert cp["premium_to_median"] > 0        # 茅台 PE30 高于同业中位
    # 茅台净利率 52% 远高于同业 → margin 调整必须为正
    margin_adj = next((a for a in cp["adjustments"] if a["kind"] == "margin"), None)
    assert margin_adj and margin_adj["adjustment"] > 0
    assert cp["adjusted_multiple"] is not None
    assert cp["implied_price"] is not None


def test_comps_picks_ps_for_lossmaking():
    """亏损公司 PE 无意义，必须强制切 PS。"""
    item = {"pe_ttm": -50.0, "ps_ttm": 8.0, "net_margin": -0.15}
    assert C.pick_multiple("科技-软件", item) == "ps_ttm"


def test_comps_picks_pb_for_banks():
    item = {"pe_ttm": 6.0, "pb": 0.7, "net_margin": 0.30}
    assert C.pick_multiple("银行", item) == "pb"


def test_comps_unavailable_when_no_peers():
    cp = C.build_comps({"available": False, "_note": "样本不足"},
                       {"industry_tag": "x"})
    assert cp["available"] is False


def test_comps_fx_note_on_cross_currency():
    items = [{"currency": "CNY", "is_target": True, "pe_ttm": 30, "net_margin": 0.5},
             {"currency": "USD", "is_target": False, "pe_ttm": 25, "net_margin": 0.3},
             {"currency": "HKD", "is_target": False, "pe_ttm": 20, "net_margin": 0.28}]
    note = C._fx_note(items)
    assert note and "汇率" in note


def test_comps_no_fx_note_single_currency():
    items = [{"currency": "CNY", "is_target": True},
             {"currency": "CNY", "is_target": False}]
    assert C._fx_note(items) is None
