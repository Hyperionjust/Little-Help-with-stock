"""三表标准化 + 字段级财报合并 + 运行清单的单元测试。"""
from __future__ import annotations
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import statements as ST  # noqa: E402
import fetch_data as FD  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")


# ── merge_financials：字段级合并（修"整块第一个获胜"）────────────────

def test_merge_financials_fills_gaps_across_providers():
    """核心场景：主源有 90% 的利润表，后备源恰好有缺的那 10%。

    原版行为是第一个返回 annual 的 provider 独占，后备源没有机会补。
    """
    a = ("provA", {"annual": [{"period": "2024FY", "revenue": 100.0, "net_income": 20.0}]})
    b = ("provB", {"annual": [{"period": "2024FY", "revenue": 999.0, "ocf": 22.0,
                               "total_assets": 150.0}]})
    out = FD.merge_financials([a, b])
    p = out["annual"][0]
    assert p["revenue"] == 100.0, "先到的源应优先，不该被后备源覆盖"
    assert p["ocf"] == 22.0, "后备源必须能补上主源缺的字段"
    assert p["total_assets"] == 150.0
    assert p["_field_sources"]["revenue"] == "provA"
    assert p["_field_sources"]["ocf"] == "provB"


def test_merge_financials_flags_mixed_source_periods():
    """混源填补是被允许的，但必须留痕——不同源的重述处理可能不同。"""
    a = ("provA", {"annual": [{"period": "2024FY", "revenue": 100.0}]})
    b = ("provB", {"annual": [{"period": "2024FY", "ocf": 22.0}]})
    out = FD.merge_financials([a, b])
    assert "2024FY" in out["mixed_source_periods"]


def test_merge_financials_single_source_not_flagged_mixed():
    a = ("provA", {"annual": [{"period": "2024FY", "revenue": 100.0, "ocf": 20.0}]})
    out = FD.merge_financials([a])
    assert out["mixed_source_periods"] == []


def test_merge_financials_sorts_periods_descending():
    a = ("p", {"annual": [{"period": "2022FY", "revenue": 1.0},
                          {"period": "2024FY", "revenue": 3.0},
                          {"period": "2023FY", "revenue": 2.0}]})
    out = FD.merge_financials([a])
    assert [x["period"] for x in out["annual"]] == ["2024FY", "2023FY", "2022FY"]


def test_merge_financials_empty_input():
    assert FD.merge_financials([])["annual"] == []


# ── build_statements：三表整形 + 诚实 coverage ──────────────────────

def _raw(annual, currency="CNY"):
    return {"resolution": {"currency": currency},
            "financials": {"source": "t", "accounting_standard": "CAS", "annual": annual}}


def test_statements_buckets_fields_into_three_tables():
    raw = _raw([{"period": "2024FY", "revenue": 100.0, "net_income": 20.0,
                 "total_assets": 150.0, "equity": 90.0, "ocf": 22.0, "capex": -5.0}])
    st = ST.build_statements(raw)
    p = st["annual"][0]
    assert p["income_statement"]["revenue"] == 100.0
    assert p["balance_sheet"]["total_assets"] == 150.0
    assert p["cash_flow"]["ocf"] == 22.0


def test_statements_derives_only_within_identities():
    """派生仅限恒等式内，且必须留痕。"""
    raw = _raw([{"period": "2024FY", "revenue": 100.0, "cogs": 40.0,
                 "net_income": 20.0, "ocf": 22.0, "capex": -5.0}])
    p = ST.build_statements(raw)["annual"][0]
    assert p["income_statement"]["gross_profit"] == 60.0      # revenue - cogs
    assert p["cash_flow"]["fcf"] == 17.0                       # ocf + capex
    assert p["cash_flow"]["net_income"] == 20.0                # 间接法起点
    derived = p["_meta"]["derived"]
    assert any("gross_profit" in d for d in derived)
    assert any("fcf" in d for d in derived)


def test_statements_never_fabricates_missing_fields():
    """缺数就是缺数——绝不补零。这是 model_builder 降级判断的依据。"""
    raw = _raw([{"period": "2024FY", "revenue": 100.0}])
    p = ST.build_statements(raw)["annual"][0]
    assert "total_assets" not in p["balance_sheet"]
    assert "ocf" not in p["cash_flow"]


def test_coverage_reports_missing_line_items_honestly():
    raw = _raw([{"period": "2024FY", "revenue": 100.0, "net_income": 20.0}])
    cov = ST.build_statements(raw)["coverage"]
    assert "total_assets" in cov["missing_line_items"]
    assert "ocf" in cov["missing_line_items"]


def test_coverage_blocks_l2_when_years_insufficient():
    """年报不足 3 年 → 不足以支撑三表建模。"""
    full = {"revenue": 100.0, "net_income": 20.0, "total_assets": 150.0,
            "total_liabilities": 60.0, "equity": 90.0, "cash": 30.0,
            "ocf": 22.0, "operating_income": 25.0}
    raw = _raw([dict(full, period="2024FY"), dict(full, period="2023FY")])
    cov = ST.build_statements(raw)["coverage"]
    assert cov["sufficient_for_l2"] is False
    assert any("年报仅 2 年" in b for b in cov["l2_blockers"])


def test_coverage_blocks_l2_when_key_line_missing():
    partial = {"revenue": 100.0, "net_income": 20.0, "total_assets": 150.0,
               "total_liabilities": 60.0, "equity": 90.0,
               "ocf": 22.0, "operating_income": 25.0}      # 缺 cash
    raw = _raw([dict(partial, period=f"202{i}FY") for i in (4, 3, 2)])
    cov = ST.build_statements(raw)["coverage"]
    assert cov["sufficient_for_l2"] is False
    assert any("cash" in b for b in cov["l2_blockers"])


def test_coverage_allows_l2_when_complete():
    full = {"revenue": 100.0, "net_income": 20.0, "total_assets": 150.0,
            "total_liabilities": 60.0, "equity": 90.0, "cash": 30.0,
            "ocf": 22.0, "operating_income": 25.0}
    raw = _raw([dict(full, period=f"202{i}FY") for i in (4, 3, 2)])
    cov = ST.build_statements(raw)["coverage"]
    assert cov["sufficient_for_l2"] is True
    assert "l2_blockers" not in cov


def test_statement_meta_declares_unit_and_currency():
    """单位必须显式声明为 million（engine 内部口径）。"""
    raw = _raw([{"period": "2024FY", "revenue": 100.0}], currency="HKD")
    m = ST.build_statements(raw)["annual"][0]["_meta"]
    assert m["unit"] == "million"
    assert m["currency"] == "HKD"
    assert m["accounting_standard"] == "CAS"


# ── run_manifest：可复现性 ─────────────────────────────────────────

@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    out = tmp_path_factory.mktemp("mf")
    fx = os.path.join(FIXTURES, "offline_fail.json")   # 降级场景，诊断最丰富
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "engine", "run_analysis.py"), "f",
         "--offline-fixture", fx, "--outdir", str(out), "--today", "2025-06-30"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    return json.load(open(os.path.join(out, "run_manifest.json"), encoding="utf-8"))


def test_manifest_records_gate_results(manifest):
    assert "missing_core_field" in manifest["gates"]["quality_gate"]["critical"]


def test_manifest_diagnostics_carry_actionable_guidance(manifest):
    """诊断必须同时带 agent_action（约束 LLM）与 user_action（引导用户）。"""
    ds = manifest["diagnostics"]
    assert ds, "降级场景应产出诊断"
    d = ds[0]
    assert d["code"] == "ALL_PROVIDERS_EMPTY"
    assert d["agent_action"], "缺 agent_action，LLM 会无脑重试或静默跳过"
    assert d["user_action_zh"] and d["user_action_en"], "双语 user_action 是引导层的内容源"
    assert not d.get("_unregistered"), "错误码未在 references/error-codes.json 登记"


def test_manifest_hashes_all_artifacts(manifest):
    """三份产物哈希——可验证是否同一次运行的产物，防止旧数据配新报告。"""
    arts = manifest["artifacts"]
    assert set(arts) >= {"analysis_json", "dashboard_html", "workbook_xlsx"}
    for k, v in arts.items():
        assert v["hash"].startswith("sha256:"), k


def test_manifest_records_environment_fingerprint(manifest):
    env = manifest["env"]
    assert env["python"]
    assert "adapters" in env and isinstance(env["adapters"], dict)
    assert env["engine_schema_version"] == "1.1.0"


def test_manifest_records_stage_timings(manifest):
    assert set(manifest["stages_ms"]) >= {"fetch", "compute_and_gate", "render"}


# ── 错误码注册表自身的完整性 ────────────────────────────────────────

def test_all_error_codes_have_both_actions():
    path = os.path.join(ROOT, "references", "error-codes.json")
    codes = json.load(open(path, encoding="utf-8"))["codes"]
    for name, c in codes.items():
        assert c.get("agent_action"), f"{name} 缺 agent_action"
        assert c.get("user_action_zh"), f"{name} 缺中文 user_action"
        assert c.get("user_action_en"), f"{name} 缺英文 user_action"
        assert c.get("severity") in ("critical", "warning", "info"), name
        assert c.get("layer") in ("engine", "analyst", "publisher"), name
