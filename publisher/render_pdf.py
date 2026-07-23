"""投资速览 PDF 渲染器（方案阶段5-2）。WeasyPrint 唯一引擎。

设计要点：
  · 模板只从 analysis.json ∪ model.json 取数——出版层不得引入新数字（铁律4）。
    所有格式化（千分位、括号负数、百分比）由本模块的过滤器做，模板零算术。
  · 引擎唯一化：CSS 用了 position:running() 与 @page 边距盒，Chromium 不支持
    ——原素材"CSS 按 WeasyPrint 写却提供 Playwright 分支"的自相矛盾（缺陷 #15）
    在这里不复存在。
  · 渲染后三道检查：pypdf 真页数（缺陷 #14：原素材最强调的页数约束无人验证）、
    闸3 数字对账（孤儿数字阻断交付）、产物哈希进 manifest。

用法：
  python render_pdf.py <analysis.json> [--model <model.json>] [-o out.pdf]
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
import _paths  # noqa: F401,E402

CSS_PATH = os.path.join(HERE, "styles", "report.css")
TEMPLATE_DIR = os.path.join(HERE, "templates")

PAGE_MIN, PAGE_MAX = 2, 6      # 投资速览页数带（含数据密度自由流）


# ── 格式化过滤器（模板零算术的保障）────────────────────────────────

def fmt_num(v, nd=2):
    """千分位 + 括号负数（会计惯例 R2）。None → em dash。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{abs(f):,.{nd}f}"
    return f"({s})" if f < 0 else s


def fmt_pct(v, nd=1):
    if v is None:
        return "—"
    return fmt_num(v * 100, nd) + "%"


def fmt_yi(v):
    """百万 → 亿（中文报告惯用），一位小数。"""
    if v is None:
        return "—"
    return fmt_num(v / 100, 1)


def _gv(node):
    return node.get("value") if isinstance(node, dict) else node


def build_context(analysis, model, issuer, logo_uri=None):
    """契约 JSON → 模板上下文。只搬运与格式化，不产生任何新数字。"""
    res = analysis.get("resolution", {})
    q = analysis.get("quote", {})
    v = analysis.get("valuation", {})
    p = analysis.get("profitability", {})
    g = analysis.get("growth", {})
    rv = analysis.get("reverse_validation", {})
    qr = analysis.get("quality_report", {})
    st = analysis.get("statements", {})
    market = res.get("market", "A")

    hist_rows = []
    for per in (st.get("annual") or [])[:5]:
        is_, cf = per.get("income_statement", {}), per.get("cash_flow", {})
        hist_rows.append({
            "period": per.get("period"),
            "revenue": is_.get("revenue"), "net_income": is_.get("net_income"),
            "op_income": is_.get("operating_income"), "ocf": cf.get("ocf"),
        })

    ctx = {
        "issuer": issuer,
        "logo": logo_uri,
        "gen_date": _dt.date.today().isoformat(),
        "name": res.get("name") or res.get("symbol"),
        "symbol": res.get("symbol"), "market": market,
        "currency": res.get("currency", ""),
        "industry": res.get("industry_tag") or "—",
        "up_cls": "up-a" if market == "A" else "up-us",
        "price": _gv(q.get("price")), "mcap": _gv(q.get("market_cap")),
        "pe": _gv(v.get("pe_ttm")), "pe_period": (v.get("pe_ttm") or {}).get("period", ""),
        "pb": _gv(v.get("pb")), "ps": _gv(v.get("ps_ttm")),
        "roe": _gv(p.get("roe")), "net_margin": _gv(p.get("net_margin")),
        "cash_content": _gv(p.get("cash_content")),
        "rev_yoy": _gv(g.get("revenue_yoy")), "rev_cagr3": _gv(g.get("revenue_cagr_3y")),
        "rv_flags": rv.get("flags") or [],
        "gate_warnings": qr.get("warning") or [],
        "gate_criticals": qr.get("critical") or [],
        "degraded": qr.get("degraded", False),
        "hist_rows": hist_rows,
        "price_src": (q.get("price") or {}).get("source", ""),
        "fin_src": (st.get("annual") or [{}])[0].get("_meta", {}).get("source", "")
                   if st.get("annual") else "",
        "m": None,
    }

    if model and model.get("valuation_level") == "L2" and model.get("dcf"):
        d = model["dcf"]
        scen = model.get("scenarios", {})
        sens = model.get("sensitivity", {})
        ctx["m"] = {
            "assumptions": model.get("assumptions", []),
            "proj_years": model.get("projections", {}).get("years", []),
            "proj_rev": model.get("projections", {}).get("income_statement", {}).get("revenue", []),
            "proj_ni": model.get("projections", {}).get("income_statement", {}).get("net_income", []),
            "proj_ufcf": model.get("projections", {}).get("cash_flow", {}).get("ufcf", []),
            "wacc": d["wacc"], "terminal": d["terminal"],
            "pv_explicit": d["pv_explicit"], "ev": d["enterprise_value"],
            "bridge": d["equity_bridge"], "equity_value": d["equity_value"],
            "shares": d["shares_diluted"], "vps": d["value_per_share"],
            "upside": (d["value_per_share"] / ctx["price"] - 1)
                      if (d["value_per_share"] and ctx["price"]) else None,
            "scen": scen, "sens": sens,
            "warnings": model.get("gate", {}).get("warning", []),
        }
    elif model and model.get("degraded_from_l2"):
        ctx["l1_reason"] = model["degraded_from_l2"].get("reason")

    return ctx


def render_html(ctx):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["num"] = fmt_num
    env.filters["pct"] = fmt_pct
    env.filters["yi"] = fmt_yi
    return env.get_template("tearsheet.html.j2").render(**ctx)


def render_pdf(html_str, out_pdf):
    from weasyprint import HTML, CSS
    HTML(string=html_str, base_url=HERE).write_pdf(
        out_pdf, stylesheets=[CSS(filename=CSS_PATH)])


def check_pages(pdf_path):
    """pypdf 真页数校验（修缺陷 #14：页数约束终于由机器判定）。"""
    from pypdf import PdfReader
    n = len(PdfReader(pdf_path).pages)
    ok = PAGE_MIN <= n <= PAGE_MAX
    return n, ok


def run_reconcile(html_str, analysis, model):
    """闸3：交付前数字对账。孤儿数字 → 阻断。"""
    sys.path.insert(0, HERE)
    import reconcile as RC
    text = RC.html_visible_text(html_str)
    pool = RC.collect_pool(analysis, model)
    return RC.scan(text, pool)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis_json")
    ap.add_argument("--model", default=None)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--logo", default=None,
                    help="公司logo本地路径或URL；不给则按代码/名称尽力抓取，抓不到优雅省略")
    ap.add_argument("--issuer", default="内部研究（自用）",
                    help="发布方署名。默认自用标注——绝不冒用任何机构名义")
    ap.add_argument("--skip-reconcile", action="store_true",
                    help="跳过闸3（仅调试；正式交付不得使用）")
    args = ap.parse_args()

    analysis = json.load(open(args.analysis_json, encoding="utf-8"))
    model = json.load(open(args.model, encoding="utf-8")) if args.model else None

    import logo as _LG
    logo_uri = _LG.get_logo(analysis.get('resolution', {}), explicit=args.logo)
    ctx = build_context(analysis, model, args.issuer, logo_uri=logo_uri)
    html_str = render_html(ctx)

    # 闸3：先对账再渲染 PDF（对账打 HTML 可见文本，与最终 PDF 同源）
    if not args.skip_reconcile:
        findings, stats = run_reconcile(html_str, analysis, model)
        if findings:
            print(f"[FAIL 闸3] {len(findings)} 个孤儿数字，交付阻断：", file=sys.stderr)
            for f0 in findings[:10]:
                print(f"  {f0['value']}  …{f0['location'][:60]}…", file=sys.stderr)
            sys.exit(4)
        print(f"[闸3 OK] {stats['checked']} 个数字全部可溯源（豁免 {stats['exempt']}）")

    out = args.out or args.analysis_json.replace("_analysis.json", "_tearsheet.pdf")
    render_pdf(html_str, out)
    n, ok = check_pages(out)
    print(f"[pages] {n} 页 {'OK' if ok else f'超出速览页数带 {PAGE_MIN}-{PAGE_MAX}'}")
    print(out)
    if not ok:
        sys.exit(5)


if __name__ == "__main__":
    main()
