"""契约校验的单元测试（闸1 + 闸2）。

为什么这个文件很重要：
stock-metrics-pro 原版的 run_regression.py 在测试目录不存在时**仍报 GREEN**，
使"回归通过"这件事失去意义。本套件是补上那个空洞的第一块砖——
每加一个契约约束，就在这里加一条负向用例证明它真的会拦。
"""
from __future__ import annotations
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "contracts"))
import validate as V  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")


def _run_engine(tmp_path, fixture="aapl"):
    """跑一次引擎，拿到真实产出。契约测试必须打真实产出，不打手搓样本。"""
    import subprocess
    fx = os.path.join(FIXTURES, f"{fixture}.json")
    today = json.load(open(fx, encoding="utf-8")).get("_snapshot_date", "2025-01-15")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "engine", "run_analysis.py"), fixture,
         "--offline-fixture", fx, "--outdir", str(tmp_path), "--today", today],
        capture_output=True, text=True)
    assert r.returncode == 0, f"引擎崩溃：{r.stderr[-500:]}"
    jf = [f for f in os.listdir(tmp_path) if f.endswith("_analysis.json")]
    assert jf, "未产出 analysis.json"
    return json.load(open(os.path.join(tmp_path, jf[0]), encoding="utf-8"))


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    return _run_engine(tmp_path_factory.mktemp("engine_out"))


# ── 闸1：analysis.json ────────────────────────────────────────────────

def test_real_engine_output_passes_contract(analysis):
    """正向：真实引擎产出必须通过契约。这条挂了说明引擎与契约脱节。"""
    assert V.validate_analysis(analysis) == []


@pytest.mark.parametrize("name,mutate,expect_in", [
    ("溯源三件套被拆",
     lambda d: d["quote"].__setitem__("price", 999.0),
     "is not of type 'object'"),
    ("必填顶层块缺失",
     lambda d: d.pop("quality_report"),
     "required property"),
    ("schema_version 漂移",
     lambda d: d.__setitem__("schema_version", "0.9"),
     "was expected"),
    ("复权口径非法枚举",
     lambda d: d["meta"].__setitem__("adjust_mode", "bogus"),
     "is not one of"),
])
def test_contract_catches_violations(analysis, name, mutate, expect_in):
    """负向：每条契约约束都必须真的会拦。"""
    bad = copy.deepcopy(analysis)
    mutate(bad)
    errs = V.validate_analysis(bad)
    assert errs, f"{name} 未被拦下——契约形同虚设"
    assert any(expect_in in e for e in errs), f"{name} 报错信息不符预期：{errs[:2]}"


def test_annotated_value_requires_full_provenance(analysis):
    """三件套是硬约束：缺任一项都不算合规。"""
    for missing in ["source", "as_of", "formula"]:
        bad = copy.deepcopy(analysis)
        bad["quote"]["price"].pop(missing, None)
        assert V.validate_analysis(bad), f"quote.price 缺 {missing} 却通过了校验"


# ── 闸2：model.json 不变量 ────────────────────────────────────────────

def _minimal_model(**over):
    m = {
        "schema_version": "1.0.0",
        "valuation_level": "L1",
        "source_analysis_path": "x_analysis.json",
        "source_analysis_hash": "sha256:deadbeef",
        "assumptions": [
            {"id": "g", "label": "永续增长", "value": 0.03,
             "source_type": "benchmark", "basis": "中国名义GDP收敛区间"}
        ],
        "gate": {"passed": True, "degraded": False, "critical": [], "warning": []},
    }
    m.update(over)
    return m


def test_minimal_model_passes_both_gates():
    m = _minimal_model()
    assert V.validate_model(m) == []
    passed, failures = V.gate2(m)
    assert passed, failures


def test_gate2_catches_scenario_probability_not_summing_to_one():
    m = _minimal_model(scenarios={"probability_sum": 0.95})
    passed, failures = V.gate2(m)
    assert not passed
    assert any("概率和" in inv for inv, _ in failures)


def test_gate2_catches_double_counted_cash_in_equity_bridge():
    """原素材的真实缺陷：净负债已含全部现金，却又加回超额现金。"""
    m = _minimal_model(dcf={
        "wacc": {"risk_free": 0.026, "erp": 0.065, "beta": 1.1,
                 "cost_of_equity": 0.098, "wacc": 0.098},
        "mid_year_convention": True,
        "ufcf": [1.0], "terminal": {"method": "gordon_growth", "g": 0.03,
                                    "tv": 10.0, "pv_tv": 8.0, "tv_pct_of_ev": 0.7},
        "enterprise_value": 10.0,
        "equity_bridge": {"less_net_debt": 2.0, "add_excess_cash": 1.0},
        "equity_value": 9.0, "shares_diluted": 1.0, "value_per_share": 9.0,
    })
    passed, failures = V.gate2(m)
    assert not passed
    assert any("重复计现金" in inv for inv, _ in failures)


def test_gate2_catches_missing_mid_year_convention():
    """期中折现缺失会系统性低估约 WACC/2。"""
    m = _minimal_model(dcf={
        "wacc": {"risk_free": 0.026, "erp": 0.065, "beta": 1.1,
                 "cost_of_equity": 0.098, "wacc": 0.098},
        "mid_year_convention": False,
        "ufcf": [1.0], "terminal": {"method": "gordon_growth", "g": 0.03,
                                    "tv": 10.0, "pv_tv": 8.0, "tv_pct_of_ev": 0.7},
        "enterprise_value": 10.0, "equity_bridge": {"less_net_debt": 2.0},
        "equity_value": 8.0, "shares_diluted": 1.0, "value_per_share": 8.0,
    })
    passed, failures = V.gate2(m)
    assert not passed
    assert any("期中折现" in inv for inv, _ in failures)


def test_gate2_catches_terminal_growth_ge_wacc():
    m = _minimal_model(dcf={
        "wacc": {"risk_free": 0.026, "erp": 0.065, "beta": 1.1,
                 "cost_of_equity": 0.05, "wacc": 0.05},
        "mid_year_convention": True,
        "ufcf": [1.0],
        "terminal": {"method": "gordon_growth", "g": 0.06,
                     "tv": 10.0, "pv_tv": 8.0, "tv_pct_of_ev": 0.7},
        "enterprise_value": 10.0, "equity_bridge": {"less_net_debt": 2.0},
        "equity_value": 8.0, "shares_diluted": 1.0, "value_per_share": 8.0,
    })
    passed, failures = V.gate2(m)
    assert not passed
    assert any("永续增长 < WACC" in inv for inv, _ in failures)


def test_gate2_catches_unbased_user_assumption():
    """主观假设必须有取值依据——这是医药估值诚实性的底线。"""
    m = _minimal_model(assumptions=[
        {"id": "peak_sales", "label": "峰值销售", "value": 3000,
         "source_type": "user_assumption"}
    ])
    passed, failures = V.gate2(m)
    assert not passed
    assert any("取值依据" in inv for inv, _ in failures)


def test_gate2_catches_release_blocker():
    m = _minimal_model(gate={"passed": True, "degraded": False, "critical": [],
                             "warning": [], "release_blocker": True})
    passed, failures = V.gate2(m)
    assert not passed
    assert any("发布阻断" in inv for inv, _ in failures)


def test_gate2_catches_untied_key_figure():
    m = _minimal_model(key_figures=[
        {"id": "K1", "metric": "目标价", "value": 100, "tie_out_status": "untied"}
    ])
    passed, failures = V.gate2(m)
    assert not passed
    assert any("tie" in inv for inv, _ in failures)


def test_gate2_catches_fed_in_historical_stats():
    """历史估值带的 mean/std/percentile 必须脚本从序列算，不接受直接喂入。"""
    m = _minimal_model(historical_band={"metric": "pe_ttm", "current_percentile": 0.72,
                                        "computed_from_series": False})
    passed, failures = V.gate2(m)
    assert not passed
    assert any("统计量由脚本计算" in inv for inv, _ in failures)


def test_model_schema_rejects_bad_valuation_level():
    m = _minimal_model(valuation_level="L3")
    assert V.validate_model(m)


def test_model_schema_rejects_risk_probability_out_of_range():
    m = _minimal_model(risks=[{"id": "R1", "category": "company", "name": "x",
                               "probability": 1.5, "impact": 0.2}])
    assert V.validate_model(m)
