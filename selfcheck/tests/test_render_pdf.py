"""出版层（速览 PDF）单元测试（方案阶段5）。

自检修掉的三个对账 bug 固化为回归：
  B6 括号负数丢符号：净负债 -51,464 百万 → "(514.6) 亿" → 正则读 +514.6 误判孤儿
     → 值池全部变体对称取负
  B7 超百比率漏变体：TV/EV=1.848 → "185%" 无 ×100 形态 → 误判孤儿
     → 比率百分数形态覆盖到 av<20
  B8 文案常量无契约出处："150%" 搜索上限只活在文案里 → 写进 current_implied
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in [os.path.join(ROOT, "engine"), os.path.join(ROOT, "analyst"),
          os.path.join(ROOT, "publisher")]:
    sys.path.insert(0, p)

import render_pdf as RP  # noqa: E402
import reconcile as RC  # noqa: E402
import dcf as DCF  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")

weasyprint = pytest.importorskip("weasyprint")


def _pipeline(snapshot):
    a = json.load(open(os.path.join(FIXTURES, snapshot), encoding="utf-8"))
    m = DCF.build_full_model(a, snapshot)
    ctx = RP.build_context(a, m, issuer="单元测试")
    html = RP.render_html(ctx)
    return a, m, html


@pytest.fixture(scope="module")
def moutai():
    return _pipeline("l2_moutai_snapshot.json")


@pytest.fixture(scope="module")
def cambricon():
    return _pipeline("l2_cambricon_snapshot.json")


# ── 格式化过滤器（模板零算术的地基）─────────────────────────────────

@pytest.mark.parametrize("v,expect", [
    (1234.5, "1,234.50"), (-1234.5, "(1,234.50)"),   # 括号负数（会计惯例）
    (None, "—"), (0, "0.00"),
])
def test_fmt_num(v, expect):
    assert RP.fmt_num(v) == expect


def test_fmt_yi_converts_million_to_yi():
    assert RP.fmt_yi(1622656) == "16,226.6"     # 茅台市值：百万 → 亿


# ── 渲染产物必须过闸3（零孤儿）────────────────────────────────────────

def test_moutai_tearsheet_zero_orphans(moutai):
    a, m, html = moutai
    findings, stats = RC.scan(RC.html_visible_text(html), RC.collect_pool(a, m))
    assert not findings, findings[:3]
    assert stats["checked"] > 80, "速览应含大量可对账数字"


def test_cambricon_tearsheet_zero_orphans(cambricon):
    a, m, html = cambricon
    findings, stats = RC.scan(RC.html_visible_text(html), RC.collect_pool(a, m))
    assert not findings, findings[:3]


# ── B6/B7 回归 ───────────────────────────────────────────────────────

def test_paren_negative_variant_symmetry():
    """B6：负值的所有单位变体都要有正形态（括号表达会吞符号）。"""
    pool = RC.collect_pool({"net_debt": -51464.34})
    assert RC.num_in_pool(514.6, pool, abs_tol=0.05)    # (514.6) 亿
    assert RC.num_in_pool(-514.6, pool, abs_tol=0.05)


def test_over_100pct_ratio_variant():
    """B7：占比 >100% 的比率也要有百分数形态。"""
    pool = RC.collect_pool({"tv_pct": 1.848})
    assert RC.num_in_pool(185.0, pool, abs_tol=0.5)     # "185%"（整数精度）


def test_search_ceiling_in_contract(cambricon):
    """B8：文案引用的 150% 上限必须有契约出处。"""
    _, m, _ = cambricon
    ci = m["scenarios"]["current_implied"]
    assert ci.get("search_ceiling_growth") == 1.5


# ── 诚实性内容断言 ───────────────────────────────────────────────────

def test_cambricon_html_shows_warnings_before_valuation(cambricon):
    """警告卡必须出现在估值数字之前——最重要的信息不藏在报告尾部。"""
    _, _, html = cambricon
    text = RC.html_visible_text(html)
    assert text.find("tv_dominates_ev") < text.find("DCF 估值")


def test_issuer_is_configurable_and_no_kimi_brand(moutai):
    """署名可配置，且绝不出现 Kimi Research 品牌（合规 §11.3.A）。"""
    _, _, html = moutai
    assert "单元测试" in html
    assert "Kimi" not in html and "kimi" not in html


def test_disclaimer_present(moutai):
    _, _, html = moutai
    assert "不构成任何投资建议" in html


# ── PDF 物理产物 ─────────────────────────────────────────────────────

def test_pdf_renders_and_page_count(tmp_path, moutai):
    _, _, html = moutai
    out = str(tmp_path / "t.pdf")
    RP.render_pdf(html, out)
    n, ok = RP.check_pages(out)
    assert ok, f"页数 {n} 超出速览带 {RP.PAGE_MIN}-{RP.PAGE_MAX}"
    assert os.path.getsize(out) > 10000, "PDF 异常小，可能渲染失败"
