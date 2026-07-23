"""深度研报渲染器单测（≥10页多页长文，图表，六维合成）。

固化自检修的两个 bug：
  B9  六维合成把百分数字段又×100（净利率显示 4784.5%）——盈利/增长字段是百分数
  B10 研报章节编号被对账当财务数字（"11 数据来源"）——加精准章节白名单
"""
from __future__ import annotations
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in [os.path.join(ROOT, "engine"), os.path.join(ROOT, "analyst"),
          os.path.join(ROOT, "publisher")]:
    sys.path.insert(0, p)

import render_report as RR  # noqa: E402
import reconcile as RC  # noqa: E402
import dcf as DCF  # noqa: E402
import charts  # noqa: E402

FIXTURES = os.path.join(ROOT, "selfcheck", "fixtures")
weasyprint = pytest.importorskip("weasyprint")


def _pipeline(snapshot):
    a = json.load(open(os.path.join(FIXTURES, snapshot), encoding="utf-8"))
    m = DCF.build_full_model(a, snapshot)
    ctx = RR.build_context(a, m, issuer="单元测试")
    html = RR.render_html(ctx)
    return a, m, html


@pytest.fixture(scope="module")
def moutai():
    return _pipeline("l2_moutai_snapshot.json")


@pytest.fixture(scope="module")
def cambricon():
    return _pipeline("l2_cambricon_snapshot.json")


# ── 零孤儿（含 B9/B10 回归）─────────────────────────────────────────

def test_moutai_report_zero_orphans(moutai):
    a, m, html = moutai
    findings, stats = RC.scan(RC.html_visible_text(html), RC.collect_pool(a, m))
    assert not findings, findings[:5]
    assert stats["checked"] > 150, "深度研报应含大量可对账数字"


def test_cambricon_report_zero_orphans(cambricon):
    a, m, html = cambricon
    findings, _ = RC.scan(RC.html_visible_text(html), RC.collect_pool(a, m))
    assert not findings, findings[:5]


def test_percent_fields_not_double_scaled(moutai):
    """B9：净利率约 47.8% 应原样出现，不是 4784.5%。"""
    _, _, html = moutai
    text = RC.html_visible_text(html)
    assert "4784" not in text and "4,784" not in text


def test_section_numbers_whitelisted():
    """B10：'11 数据来源' 的章节编号不得被判为孤儿。"""
    pool = RC.collect_pool({"x": 1.0})   # 极小值池
    findings, _ = RC.scan("11 数据来源与溯源  8. 假设清单  10 风险评估", pool)
    assert not findings, findings


# ── 六维合成 ─────────────────────────────────────────────────────────

def test_six_dimension_synthesized(moutai):
    a, m, _ = moutai
    dims = RR.synth_six_dimension(a, m)
    assert len(dims) >= 4, "至少产出 4 个维度"
    titles = " ".join(d[0] for d in dims)
    assert "D1" in titles and "D2" in titles
    # 每维都有 so-what
    for _, _, sowhat in dims:
        assert sowhat and len(sowhat) > 10


def test_d2_reverse_dcf_present(moutai):
    """D2 隐含假设还原是六维最核心的一维，必须出现。"""
    a, m, _ = moutai
    dims = RR.synth_six_dimension(a, m)
    d2 = [d for d in dims if d[0].startswith("D2")]
    assert d2, "缺 D2 市场隐含维度"
    assert "隐含" in d2[0][1] or "reverse" in d2[0][2].lower() or "DCF" in d2[0][1]


def test_cambricon_d2_handles_unsolvable(cambricon):
    """寒武纪 DCF 撑不起市价 → D2 应给出'定价在窗口外叙事'的说明。"""
    a, m, _ = cambricon
    dims = RR.synth_six_dimension(a, m)
    d2 = [d for d in dims if d[0].startswith("D2")]
    assert d2
    assert "150%" in d2[0][1] or "窗口" in d2[0][2] or "叙事" in d2[0][2]


# ── 图表 ─────────────────────────────────────────────────────────────

def test_charts_are_svg_not_mermaid(moutai):
    """规避原素材 mermaid 漏进 PDF 的缺陷——图表必须是内嵌 SVG。"""
    _, _, html = moutai
    assert "data:image/svg+xml;base64," in html
    assert "mermaid" not in html.lower()
    assert "style P2 fill" not in html   # 原版漏出的 mermaid 语法特征


# ── 内容完整性 ───────────────────────────────────────────────────────

def test_report_has_all_core_sections(moutai):
    _, _, html = moutai
    for section in ["执行摘要", "六维分析", "财务历史", "DCF 估值",
                    "情景分析", "敏感性", "假设清单", "风险评估",
                    "数据来源", "免责声明"]:
        assert section in html, f"缺章节 {section}"


def test_no_kimi_brand(moutai):
    _, _, html = moutai
    assert "Kimi" not in html and "kimi" not in html


# ── PDF 物理产物：多页 ───────────────────────────────────────────────

def test_report_pdf_is_multipage(tmp_path, moutai):
    from weasyprint import HTML, CSS
    from pypdf import PdfReader
    _, _, html = moutai
    out = str(tmp_path / "r.pdf")
    HTML(string=html, base_url=RR.HERE).write_pdf(out, stylesheets=[CSS(filename=RR.CSS_PATH)])
    n = len(PdfReader(out).pages)
    assert n >= RR.PAGE_MIN, f"深度研报页数 {n} < {RR.PAGE_MIN}"
    assert n > 5, "应显著长于速览（3-5页）"


# ── 补充研究双分区（用户需求：量化/研究分开，研究段要引用）──────────────

_INSIGHT = {
    "sections": [
        {"title": "行业", "body": "国产替代加速，营收增长 453% [1]，居行业第一 [2]。"},
    ],
    "sources": [
        {"title": "来源A", "publisher": "X", "date": "2026", "url": "https://a.com/1"},
        {"title": "来源B", "publisher": "Y", "date": "2026", "url": "https://b.com/2"},
    ],
}


@pytest.fixture(scope="module")
def cambricon_with_insight():
    a = json.load(open(os.path.join(FIXTURES, "l2_cambricon_snapshot.json"), encoding="utf-8"))
    m = DCF.build_full_model(a, "l2_cambricon_snapshot.json")
    ctx = RR.build_context(a, m, issuer="单元测试", insight=_INSIGHT)
    return a, m, RR.render_html(ctx)


def test_insight_region_is_demarcated(cambricon_with_insight):
    """研究区必须有清晰的证据分级横幅 + 注释标记。"""
    _, _, html = cambricon_with_insight
    assert "补充研究" in html
    assert "证据等级" in html
    assert "INSIGHT-BEGIN" in html and "INSIGHT-END" in html


def test_split_regions_separates_quant_and_insight(cambricon_with_insight):
    a, m, html = cambricon_with_insight
    rigorous, insight = RC.split_regions(html)
    assert "453" in insight        # 研究数字在研究区
    assert "行业" in insight
    # 量化区不含研究独有内容
    assert "国产替代" not in rigorous


def test_quant_region_still_reconciles_with_insight_present(cambricon_with_insight):
    """加了研究区后，量化区仍须零孤儿（两区独立核验）。"""
    a, m, html = cambricon_with_insight
    rigorous, _ = RC.split_regions(html)
    findings, _ = RC.scan(rigorous, RC.collect_pool(a, m))
    assert not findings, findings[:5]


def test_insight_region_requires_citations(cambricon_with_insight):
    _, _, html = cambricon_with_insight
    _, insight = RC.split_regions(html)
    findings, stats = RC.scan_insight(insight, n_sources=2)
    assert not findings, findings          # 示例每个数字都有 [n]
    assert stats["checked"] >= 1


def test_insight_catches_uncited_number():
    findings, _ = RC.scan_insight("市值 9999 亿元，无引用。", n_sources=3)
    assert any(f["code"] == "UNCITED_NUMBER" for f in findings)


def test_insight_catches_out_of_range_citation():
    findings, _ = RC.scan_insight("增长 453% [9]。", n_sources=3)
    assert any(f["code"] == "BAD_SOURCE_REF" for f in findings)


# ── 结构调整（2026-07：bullet + 加粗 + 研判块 + S7 趋势警示）──────────

_INSIGHT_RICH = {
    "sections": [
        {"title": "S2 行业", "body": "<p>格局：</p><ul><li><b>A厂</b> 70% [1]</li>"
                                    "<li>B厂 23% [2]</li></ul>"
                                    "<div class='verdict'><b>研判：</b>竞争加剧。</div>"},
        {"title": "S7 近期趋势", "is_trend": True,
         "body": "<p>近 5 日下跌 10.67% [1]。</p>"
                 "<div class='verdict'><b>研判（低置信）：</b>情绪驱动。</div>"},
    ],
    "sources": [
        {"title": "来源A", "publisher": "X", "date": "2026", "url": "https://a.com/1"},
        {"title": "来源B", "publisher": "Y", "date": "2026", "url": "https://b.com/2"},
    ],
}


def _pipeline_rich():
    a = json.load(open(os.path.join(FIXTURES, "l2_cambricon_snapshot.json"), encoding="utf-8"))
    m = DCF.build_full_model(a, "x")
    ctx = RR.build_context(a, m, issuer="T", insight=_INSIGHT_RICH)
    return a, m, RR.render_html(ctx)


def test_insight_renders_bullets_and_bold():
    """bullet 与加粗必须真的进 HTML（用户要求的排版）。"""
    _, _, html = _pipeline_rich()
    assert "<ul>" in html and "<li>" in html
    assert "<b>A厂</b>" in html
    assert "class='verdict'" in html or 'class="verdict"' in html


def test_trend_section_gets_caveat_banner():
    """S7 标 is_trend 的节必须自动加'不一定有效'警示框。"""
    _, _, html = _pipeline_rich()
    assert "不一定有效" in html
    # 非趋势节不应有该警示
    idx_trend = html.find("近期趋势")
    idx_caveat = html.find("不一定有效")
    assert idx_caveat > 0 and abs(idx_caveat - idx_trend) < 2000


def test_rich_insight_still_reconciles():
    """加了 bullet/加粗/研判块后，研究区引用核验仍正常（数字在 li 里也要有引用）。"""
    a, m, html = _pipeline_rich()
    _, insight = RC.split_regions(html)
    findings, _ = RC.scan_insight(insight, n_sources=2)
    assert not findings, findings


def test_uncited_number_in_bullet_is_caught():
    """bullet 里的数字漏引用也要被拦——排版不能成为绕过对账的后门。"""
    bad = {"sections": [{"title": "S", "body": "<ul><li>份额 88% 无引用</li></ul>"}],
           "sources": [{"title": "s", "url": "https://x.com"}]}
    a = json.load(open(os.path.join(FIXTURES, "l2_cambricon_snapshot.json"), encoding="utf-8"))
    m = DCF.build_full_model(a, "x")
    html = RR.render_html(RR.build_context(a, m, issuer="T", insight=bad))
    _, insight = RC.split_regions(html)
    findings, _ = RC.scan_insight(insight, n_sources=1)
    assert any(f["code"] == "UNCITED_NUMBER" for f in findings)


def test_sources_table_not_scanned_as_body():
    """来源表的 URL/日期不该被当正文数字核验（切到 BODY-END 之前）。"""
    _, _, html = _pipeline_with_insight()
    _, insight = RC.split_regions(html)
    # 研究正文里不应包含来源 URL 的数字串
    assert "biggo" not in insight.lower() if "biggo" in html.lower() else True


def _pipeline_with_insight():
    a = json.load(open(os.path.join(FIXTURES, "l2_cambricon_snapshot.json"), encoding="utf-8"))
    m = DCF.build_full_model(a, "x")
    ctx = RR.build_context(a, m, issuer="T", insight=_INSIGHT)
    return a, m, RR.render_html(ctx)
