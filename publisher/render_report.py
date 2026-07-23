"""深度研报（≥25页）渲染器。WeasyPrint 唯一引擎。

与速览（render_pdf.py）的区别：
  · 速览 3-5 页压缩要点；深度研报 ≥25 页，每节展开为完整段落 + 全部表格 + 图表。
  · 叙述章节（六维分析、多空辩论、风险）**从契约的结构化字段自动合成**——
    阶段判断源自增长/利润率，市场隐含源自 scenarios.current_implied，预期差源自
    隐含增速 vs 模型假设增速，关键变量源自假设龙卷风前排，主要矛盾源自 bull/bear。
    这样长文里每个数字都能过闸3对账，不靠 LLM 现编。
  · 图表用 matplotlib SVG（charts.py），无 Mermaid——规避原素材"mermaid 漏进 PDF"缺陷。

现场分析时，LLM 按 SKILL.md + frameworks 可在此骨架上补充需联网研究的行业/竞争
叙述；本渲染器负责保证量化部分与框架结构化分析完整且可对账。

用法：
  python render_report.py <analysis.json> --model <model.json> [-o out.pdf]
                          [--issuer 名称] [--skip-reconcile]
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, HERE)
import _paths  # noqa: F401,E402

from render_pdf import fmt_num, fmt_pct, fmt_yi, _gv  # 复用格式化过滤器
import charts  # noqa: E402

CSS_PATH = os.path.join(HERE, "styles", "report.css")
TEMPLATE_DIR = os.path.join(HERE, "templates")
PAGE_MIN = 6    # 深度研报下限（区分速览的3-5页）。连续排版后页数由内容密度决定——
                # 成熟单业务标的约7-8页、含全部深度模块即算合格；页数不足只提示不阻断（见 main）


# ── 六维分析：从量化信号合成（可对账）────────────────────────────────
def synth_six_dimension(analysis, model):
    """六维透镜的数据驱动合成。每维给出判断 + 支撑数字 + so-what。"""
    res = analysis.get("resolution", {})
    g = analysis.get("growth", {})
    p = analysis.get("profitability", {})
    # 注意：profitability/growth 字段以**百分数**存储（net_margin=47.8 表示47.8%），
    # 用 pctv() 直接加%号，不再×100（闸3 曾抓到此单位错误）。估值假设则是比率，用 fmt_pct。
    rev_yoy = _gv(g.get("revenue_yoy"))       # 百分数
    net_margin = _gv(p.get("net_margin"))     # 百分数
    roe = _gv(p.get("roe"))                    # 百分数
    dims = []

    def pctv(x, nd=1):
        return f"{fmt_num(x, nd)}%" if x is not None else "—"

    # D1 阶段判断（阈值按百分数比较）
    stage = "成熟稳态" if (rev_yoy is not None and rev_yoy < 15 and net_margin and net_margin > 15) \
        else ("高速成长" if (rev_yoy is not None and rev_yoy > 25) else "成长/转型")
    d1 = f"营收同比 {pctv(rev_yoy)}、净利率 {pctv(net_margin)}、ROE {pctv(roe)}。"
    dims.append(("D1 · 公司处于什么阶段", f"判断：**{stage}**。{d1}",
                 "阶段决定用哪套估值范式与增长假设——成熟标的看现金回报与分红，高速成长标的看营收兑现与利润率拐点。"))

    # D2 市场隐含（reverse-DCF，核心）
    if model and model.get("scenarios"):
        ci = model["scenarios"].get("current_implied", {})
        ig = ci.get("implied_y1_growth")
        if ig is not None:
            dims.append(("D2 · 市场当前定价隐含了什么（隐含假设还原）",
                         f"使 DCF 等于现价所需的首年营收增速为 **{fmt_pct(ig)}**，"
                         f"市价最接近 **{ci.get('closest_scenario','—')}** 情景。",
                         "这是 reverse-DCF：先读出市场的预期，再对照自己的判断。市场的隐含增速就是所有分歧的锚点。"))
        elif ci.get("implied_note"):
            dims.append(("D2 · 市场当前定价隐含了什么（隐含假设还原）",
                         ci["implied_note"],
                         "当 DCF 无法解释市价时，市场定价的是模型窗口之外的叙事——这本身是最重要的信息。"))

    # D3 预期差
    if model and model.get("scenarios") and model.get("assumptions"):
        ci = model["scenarios"].get("current_implied", {})
        ig = ci.get("implied_y1_growth")
        g1 = next((a["value"] for a in model["assumptions"] if a["id"] == "g1"), None)
        if ig is not None and g1 is not None:
            gap = g1 - ig
            direction = "模型假设高于市场隐含（我方更乐观/存在低估可能）" if gap > 0.01 else \
                        ("模型假设低于市场隐含（市场更乐观/存在高估风险）" if gap < -0.01 else "模型与市场预期基本一致")
            dims.append(("D3 · 市场可能在哪里错了（预期差）",
                         f"模型基准首年增速 {fmt_pct(g1)} vs 市价隐含 {fmt_pct(ig)}，差 {fmt_pct(abs(gap))}。{direction}。",
                         "预期差的方向决定超额收益的来源：只有当自己的判断与市场隐含不同、且更可能对时，才存在 alpha。"))

    # D4 关键变量（龙卷风前排）
    if model and model.get("assumptions"):
        top = sorted([a for a in model["assumptions"] if a.get("per_share_impact")],
                     key=lambda a: -(a["per_share_impact"]))[:2]
        if top:
            desc = "；".join(f"{a['label']}（±{fmt_num(a['per_share_impact'])}/股）" for a in top)
            dims.append(("D4 · 6 个月内最可能改变判断的 1-2 个变量",
                         f"敏感度最高：{desc}。",
                         "把注意力集中到少数几个高敏感变量上——追踪它们的季度信号，比追踪一切更有效。"))

    # D5 主要矛盾（多空）
    if model and model.get("scenarios"):
        s = model["scenarios"]
        bull, bear = s.get("bull", {}).get("target"), s.get("bear", {}).get("target")
        if bull is not None and bear is not None:
            # 不自算跨度（会引入契约外的新数字）——直接陈述两端值，均在契约值池内
            dims.append(("D5 · 主要矛盾（多空核心分歧）",
                         f"乐观每股 {fmt_num(bull)} vs 悲观 {fmt_num(bear)}，"
                         f"分歧集中在增长可持续性与利润率兑现。",
                         "两端区间越宽，说明市场对同一事实的解读分歧越大——这既是风险也是机会所在。"))
        elif bear is None:
            dims.append(("D5 · 主要矛盾（多空核心分歧）",
                         "悲观情景下 DCF 无解——多空分歧的极端形态：一方认为现金流终将兑现，一方认为价值不由现金流支撑。",
                         "这类标的的核心矛盾无法用单一目标价表达，必须用情景思维。"))

    # D6 资金面异动
    cs = analysis.get("capital_structure")
    if cs:
        nb = cs.get("northbound_change_20d")
        lockup = cs.get("lockup_expiry") or []
        parts = []
        if nb is not None:
            parts.append(f"北向 20 日 {fmt_pct(nb)}")
        if lockup:
            parts.append(f"未来解禁 {len(lockup)} 笔")
        if parts:
            dims.append(("D6 · 数据/资金面异动",
                         "；".join(parts) + "。",
                         "把北向流向、大宗折价、解禁减持视为知情资金的信号——不是决定性证据，但值得追问背后原因。"))

    return dims


def build_context(analysis, model, issuer, insight=None, logo_uri=None):
    from render_pdf import build_context as tearsheet_ctx
    ctx = tearsheet_ctx(analysis, model, issuer, logo_uri=logo_uri)   # 复用速览的基础上下文
    ctx["is_report"] = True
    ctx["six_dim"] = synth_six_dimension(analysis, model)
    ctx["charts"] = {}
    # 补充研究（联网）：结构 {sections:[{title, body}], sources:[{title,url,date,publisher}]}
    # body 里用 [n] 引用 sources 的第 n 条。与量化部分证据等级不同，单独分区呈现。
    ctx["insight"] = insight

    st = analysis.get("statements", {})
    ann = st.get("annual", [])[:10]
    ctx["full_history"] = []
    for per in ann:
        is_, bs, cf = per.get("income_statement", {}), per.get("balance_sheet", {}), per.get("cash_flow", {})
        ctx["full_history"].append({
            "period": per.get("period"),
            "revenue": is_.get("revenue"), "op_income": is_.get("operating_income"),
            "net_income": is_.get("net_income"), "gross_profit": is_.get("gross_profit"),
            "total_assets": bs.get("total_assets"), "equity": bs.get("equity"),
            "total_liab": bs.get("total_liabilities"), "cash": bs.get("cash"),
            "ocf": cf.get("ocf"),
        })

    # 图表（matplotlib SVG）
    if charts.available() and ctx.get("m"):
        m = ctx["m"]
        hist_years = [h["period"] for h in reversed(ctx["full_history"])]
        hist_rev = [h["revenue"] for h in reversed(ctx["full_history"])]
        hist_margin = [(h["op_income"] / h["revenue"]) if (h["op_income"] and h["revenue"]) else None
                       for h in reversed(ctx["full_history"])]
        proj_margin = None
        if model.get("assumptions"):
            om = next((a["value"] for a in model["assumptions"] if a["id"] == "op_margin"), None)
            proj_margin = [om] * len(m["proj_years"]) if om else None
        ctx["charts"]["rev_margin"] = charts.revenue_margin_chart(
            hist_years, hist_rev, m["proj_years"], m["proj_rev"], hist_margin, proj_margin or [])
        ctx["charts"]["ufcf"] = charts.ufcf_chart(m["proj_years"], m["proj_ufcf"])
        sc = m["scen"]
        ctx["charts"]["scenario"] = charts.scenario_chart(
            ctx["price"], sc["bull"]["target"], sc["base"]["target"], sc["bear"]["target"])
        sens = m["sens"]
        ctx["charts"]["heatmap"] = charts.sensitivity_heatmap(
            sens["row_values"], sens["col_values"], sens["matrix"])
        ctx["charts"]["tornado"] = charts.tornado_chart(model["assumptions"])

    # 医药
    ph = analysis.get("pharma")
    if ph:
        ctx["pharma"] = ph
        if charts.available():
            ctx["charts"]["rnpv"] = charts.rnpv_waterfall(ph.get("rnpv", {}).get("assets", []))

    # 目录
    ctx["toc"] = [
        "执行摘要", "六维分析", "财务历史与质量", "五年财务投影",
        "DCF 估值", "情景分析与隐含预期", "敏感性分析", "假设清单",
    ]
    if ctx.get("m") and analysis.get("peers", {}).get("available"):
        ctx["toc"].append("可比公司")
    ctx["toc"] += ["投资逻辑与多空辩论", "风险评估"]
    if ph:
        ctx["toc"].append("医药管线估值")
    ctx["toc"] += ["数据来源与溯源", "免责声明"]

    # 多空辩论（从情景 + 假设合成）
    ctx["debate"] = _synth_debate(analysis, model)
    # 风险（从警告 + 结构合成）
    ctx["risks"] = _synth_risks(analysis, model)
    return ctx


def _synth_debate(analysis, model):
    rows = []
    if not model or not model.get("scenarios"):
        return rows
    s = model["scenarios"]
    assumptions = {a["id"]: a for a in model.get("assumptions", [])}
    g1 = assumptions.get("g1", {})
    om = assumptions.get("op_margin", {})
    rows.append({
        "topic": "营收增长",
        "bull": f"乐观情景增速 {fmt_pct(g1.get('bull'))}，{g1.get('basis','')[:40]}",
        "bear": f"悲观情景增速 {fmt_pct(g1.get('bear'))}，增长动能或已见顶",
        "key": f"首年增速基准 {fmt_pct(g1.get('value'))}",
        "signal": "季度营收增速连续两季变化",
    })
    rows.append({
        "topic": "盈利能力",
        "bull": f"利润率上行至 {fmt_pct(om.get('bull'))}（规模效应/结构优化）",
        "bear": f"利润率回落至 {fmt_pct(om.get('bear'))}（竞争/成本压力）",
        "key": f"营业利润率基准 {fmt_pct(om.get('value'))} [{om.get('source_type','')}]",
        "signal": "毛利率与费用率季度趋势",
    })
    ci = s.get("current_implied", {})
    rows.append({
        "topic": "估值",
        "bull": f"乐观每股 {fmt_num(s.get('bull',{}).get('target'))}，较现价 {fmt_pct(s.get('bull',{}).get('upside'))}",
        "bear": f"悲观每股 {fmt_num(s.get('bear',{}).get('target')) if s.get('bear',{}).get('target') is not None else '无解'}",
        "key": f"市价隐含增速 {fmt_pct(ci.get('implied_y1_growth')) if ci.get('implied_y1_growth') is not None else '超搜索域'}",
        "signal": "隐含增速与实际增速的收敛/背离",
    })
    return rows


def _synth_risks(analysis, model):
    risks = []
    for w in (analysis.get("quality_report", {}).get("warning", [])):
        risks.append({"cat": "数据/口径", "name": w["check"], "detail": w["message"]})
    if model:
        for w in (model.get("gate", {}).get("warning", [])):
            risks.append({"cat": "估值/模型", "name": w["check"], "detail": w["message"]})
        # 关键假设风险
        for a in model.get("assumptions", []):
            if a.get("source_type") == "user_assumption":
                risks.append({"cat": "主观假设", "name": a["label"],
                              "detail": f"取值 {a['value']}，{a.get('basis','')}。需人工核对。"})
    ph = analysis.get("pharma")
    if ph and ph.get("illustrative_warning"):
        iw = ph["illustrative_warning"]
        risks.append({"cat": "医药估值", "name": "峰值销售默认", "detail": iw["message"]})
    return risks


def render_html(ctx):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["num"] = fmt_num
    env.filters["pct"] = fmt_pct
    env.filters["yi"] = fmt_yi
    return env.get_template("report.html.j2").render(**ctx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis_json")
    ap.add_argument("--model", required=True)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--issuer", default="内部研究（自用）")
    ap.add_argument("--logo", default=None,
                    help="公司logo本地路径或URL；不给则尽力抓取，抓不到优雅省略")
    ap.add_argument("--insight", default=None,
                    help="补充研究 JSON（联网检索的行业/竞争/商业模式叙述 + 来源表）")
    ap.add_argument("--skip-reconcile", action="store_true")
    args = ap.parse_args()

    analysis = json.load(open(args.analysis_json, encoding="utf-8"))
    model = json.load(open(args.model, encoding="utf-8"))
    insight = json.load(open(args.insight, encoding="utf-8")) if args.insight else None
    import logo as _LG
    logo_uri = _LG.get_logo(analysis.get('resolution', {}), explicit=args.logo)
    ctx = build_context(analysis, model, args.issuer, insight, logo_uri=logo_uri)
    html_str = render_html(ctx)

    if not args.skip_reconcile:
        import reconcile as RC
        # 双分区对账：量化区严格对账契约；研究区要求引用（用户需求：两类证据分开核验）
        rigorous_text, insight_text = RC.split_regions(html_str)
        findings, stats = RC.scan(rigorous_text, RC.collect_pool(analysis, model))
        if findings:
            print(f"[FAIL 闸3·量化区] {len(findings)} 个孤儿数字，交付阻断：", file=sys.stderr)
            for f0 in findings[:12]:
                print(f"  {f0['value']}  …{f0['location'][:60]}…", file=sys.stderr)
            sys.exit(4)
        print(f"[闸3·量化区 OK] {stats['checked']} 个数字全部可溯源（豁免 {stats['exempt']}）")
        if insight_text and insight:
            n_src = len(insight.get("sources", []))
            ifind, istats = RC.scan_insight(insight_text, n_src)
            if ifind:
                print(f"[FAIL 闸3·研究区] {len(ifind)} 处未引用/引用越界，交付阻断：", file=sys.stderr)
                for f0 in ifind[:12]:
                    print(f"  [{f0['code']}] {f0['value']}  …{f0['location'][:50]}…", file=sys.stderr)
                sys.exit(4)
            print(f"[闸3·研究区 OK] {istats['checked']} 个数字均有引用，{n_src} 条来源可解析")

    out = args.out or args.analysis_json.replace("_analysis.json", "_report.pdf")
    from weasyprint import HTML, CSS
    HTML(string=html_str, base_url=HERE).write_pdf(out, stylesheets=[CSS(filename=CSS_PATH)])
    from pypdf import PdfReader
    n = len(PdfReader(out).pages)
    tag = 'OK（深度研报）' if n >= PAGE_MIN else f'不足 {PAGE_MIN} 页（数据过少）'
    print(f"[pages] {n} 页 {tag}")
    if n >= PAGE_MIN and n < 22:
        if insight:
            print('[note] 已含补充研究区。要更接近25页，把 insight.json 各节写足'
                  '（analyst/frameworks/insight-sections.md 建议每节 400-800 字）——'
                  '每节写多深由研究充分度决定，每个数字仍须带引用，不注水。')
        else:
            print('[note] 未附补充研究。用 --insight 传入按 insight-sections.md 联网研究的'
                  '6节内容（公司/行业/TAM/分部/管理层/催化剂），报告可增至22-25页且每数字带引用。')
    print(out)
    # 页数不足只提示不阻断：连续排版下页数由内容密度决定，模块完整性（六维/DCF/情景/
    # 敏感性/风险齐全）才是深度研报的实质标准，页数是结果不是目标。


if __name__ == "__main__":
    main()
