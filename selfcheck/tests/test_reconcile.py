"""闸3 数字对账的单元测试。

本产品的核心承诺是"报告里每个数字都能溯源"。这套测试就是那个承诺的证据。
两类断言同等重要：
  · 编造的数字必须被拦下（守卫有效）
  · 真实产出必须零误报（守卫可用——一个天天误报的闸会被人关掉）

历史坑（已固化为回归用例）：
  1. 固定 rel=2% 让编造的 "18.7%" 命中真实存在的 19.0 而漏网
     → 改为按 token 显示精度推导容差
  2. 药物代号 SHR-1701 被数字正则当成 -1701 误报
     → 加"药物产品代号"上下文白名单
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "publisher"))
import reconcile as R  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")


@pytest.fixture(scope="module")
def moutai(tmp_path_factory):
    """跑真实引擎拿产出——对账测试必须打真实产物，不打手搓样本。"""
    out = tmp_path_factory.mktemp("rec")
    fx = os.path.join(FIXTURES, "moutai_600519.json")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "engine", "run_analysis.py"), "m",
         "--offline-fixture", fx, "--outdir", str(out), "--today", "2025-04-15"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    aj = [f for f in os.listdir(out) if f.endswith("_analysis.json")][0]
    hj = [f for f in os.listdir(out) if f.endswith("_dashboard.html")][0]
    analysis = json.load(open(os.path.join(out, aj), encoding="utf-8"))
    return analysis, os.path.join(out, hj)


@pytest.fixture(scope="module")
def pool(moutai):
    return R.collect_pool(moutai[0])


# ── 容差按显示精度推导 ────────────────────────────────────────────────

@pytest.mark.parametrize("token,expect_atol", [
    ("57", 0.5), ("18.7", 0.05), ("22.58", 0.005),
    ("1,947,000", 0.5), ("-12.3", 0.05),
])
def test_tolerance_follows_display_precision(token, expect_atol):
    atol, _ = R.tolerance_for(token)
    assert atol == pytest.approx(expect_atol, abs=1e-6)


# ── 正向：真实数字的各种合法写法都不能误报 ─────────────────────────────

@pytest.mark.parametrize("fmt", ["{:.2f}", "{:.1f}", "{:.0f}"])
def test_real_value_at_any_rounding_passes(moutai, pool, fmt):
    pe = moutai[0]["valuation"]["pe_ttm"]["value"]
    findings, _ = R.scan(f"当前 PE-TTM 为 {fmt.format(pe)} 倍。", pool)
    assert not findings, findings


def test_real_price_passes(moutai, pool):
    price = moutai[0]["quote"]["price"]["value"]
    findings, _ = R.scan(f"最新收盘 {price:.1f} 元。", pool)
    assert not findings


# ── 负向：编造的数字必须被拦下 ─────────────────────────────────────────

@pytest.mark.parametrize("text,label", [
    ("当前 PE-TTM 为 27.35 倍。", "凭空编造的 PE"),
    ("预计未来三年营收复合增速 18.7%。", "编造增速（曾因容差过宽漏网）"),
    ("综合三种方法，给予目标价 2180.00 元。", "编造目标价"),
    ("毛利率约 91.8%，同比提升 0.6 个百分点。", "LLM 顺手心算"),
    ("PE 约 23.5 倍。", "邻近真值但不等于真值"),
])
def test_fabricated_numbers_are_caught(pool, text, label):
    findings, _ = R.scan(text, pool)
    assert findings, f"{label} 未被拦下——对账闸形同虚设"
    assert findings[0]["code"] == "RECONCILE_ORPHAN_NUMBER"


# ── 白名单：按上下文豁免，不按数值豁免 ─────────────────────────────────

@pytest.mark.parametrize("text,label", [
    ("2024年公司实现营收增长，2025年一季度延续。", "四位年份"),
    ("如图表 12 所示，详见第 3 节。", "图表与章节编号"),
    ("前 5 大客户贡献显著，近 3 年保持稳定。", "枚举量词"),
    ("SHR-1701 与 SHR-A1811 已进入三期临床。", "药物代号（曾误报为 -1701）"),
    ("报告期 2025-04-15，数据截至当日。", "ISO 日期"),
    ("52 周最高价区间。", "52周惯用语"),
])
def test_context_whitelist_exempts(pool, text, label):
    findings, _ = R.scan(text, pool)
    assert not findings, f"{label} 被误报：{findings}"


# ── 端到端：真实产出必须零孤儿 ─────────────────────────────────────────

ALL_FIXTURES = ["aapl", "moutai_600519", "tencent_00700", "hengrui_600276",
                "beigene", "early_biotech", "star_incomplete_688", "offline_fail"]


@pytest.mark.parametrize("fx", ALL_FIXTURES)
def test_real_reports_have_zero_orphans(tmp_path, fx):
    """每个 fixture 的真实产出都必须零孤儿。

    这条比负向用例更重要：一个天天误报的对账闸会被人关掉，
    关掉之后所有保证归零。
    """
    fxp = os.path.join(FIXTURES, f"{fx}.json")
    today = json.load(open(fxp, encoding="utf-8")).get("_snapshot_date", "2025-01-15")
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "engine", "run_analysis.py"), fx,
         "--offline-fixture", fxp, "--outdir", str(tmp_path), "--today", today],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    aj = [f for f in os.listdir(tmp_path) if f.endswith("_analysis.json")][0]
    hj = [f for f in os.listdir(tmp_path) if f.endswith("_dashboard.html")][0]
    analysis = json.load(open(os.path.join(tmp_path, aj), encoding="utf-8"))
    findings, stats = R.reconcile(os.path.join(tmp_path, hj), analysis)
    assert not findings, f"{fx} 出现孤儿数字：{findings[:3]}"
    assert stats["checked"] > 0, f"{fx} 一个数字都没检查到，扫描器可能失效"


# ── 值池与 HTML 处理 ──────────────────────────────────────────────────

def test_pool_generates_unit_variants():
    """内部口径是百万，行文会写亿/万亿/百分数——合法变形不应误报。"""
    pool = R.collect_pool({"x": 1947000.0, "ratio": 0.226})
    for v in (1947000.0, 19470.0, 1.947, 22.6):
        assert R.num_in_pool(v, pool, abs_tol=0.05), f"{v} 未在变体池中"


def test_html_strips_embedded_json_script():
    """HTML 内嵌的 analysis JSON 不属于'读者可见数字'，必须剥掉。

    否则值池会自己命中自己，对账闸永远全绿——这是最危险的失效模式。
    """
    html = '<p>PE 为 22.58 倍</p><script id="analysis-data">{"fake": 99999}</script>'
    text = R.html_visible_text(html)
    assert "99999" not in text
    assert "22.58" in text


def test_whitelist_rate_sentinel_in_strict_mode(moutai):
    """豁免率过高说明白名单在吞噬对账面——--strict 下应视为失败。"""
    _, html_path = moutai
    analysis = moutai[0]
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "publisher", "reconcile.py"), html_path,
         "--analysis", os.path.join(os.path.dirname(html_path),
                                    [f for f in os.listdir(os.path.dirname(html_path))
                                     if f.endswith("_analysis.json")][0]),
         "--strict"],
        capture_output=True, text=True)
    # 正常产出的豁免率应远低于 60%，--strict 不应触发哨兵
    assert "豁免率" not in r.stdout, r.stdout
