"""一步到位入口：解析 → 取数 → 计算 → (医药) → 门禁 → 渲染四格式。

用法：
  python run_analysis.py "分析 600519"
  python run_analysis.py "AAPL" --offline-fixture selfcheck/fixtures/aapl.json --today 2025-01-15
输出：{symbol}_analysis.json / {symbol}_dashboard.html / {symbol}_workbook.xlsx
"""
from __future__ import annotations
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _util import load_json, dump_json  # noqa: E402
import resolve_symbol as R  # noqa: E402
import fetch_data as FD  # noqa: E402
import compute_metrics as CM  # noqa: E402
import quality_gate as QG  # noqa: E402


def strip_command_words(text):
    for w in ["分析", "看一下", "看看", "帮我", "的估值和趋势", "估值", "趋势", "这只股票", "股票"]:
        text = text.replace(w, " ")
    return text.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--offline-fixture")
    ap.add_argument("--today")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--wacc", type=float, default=0.09)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    if args.offline_fixture:
        raw = load_json(args.offline_fixture)
        resolved = raw.get("resolution")
    else:
        token = strip_command_words(args.query)
        # 取第一个像代码/名称的 token
        m = re.search(r"[A-Za-z]{1,5}|\d{4,6}|[一-龥]{2,6}", token)
        resolved = R.resolve(m.group(0) if m else token)
        raw = FD.fetch_live(resolved)

    symbol = resolved.get("symbol", "out")
    analysis = CM.compute(raw)
    analysis["meta"]["_raw_financials"] = raw.get("financials", {}).get("source")

    if resolved.get("is_pharma"):
        import pharma_valuation as PV
        analysis["pharma"] = PV.compute_pharma(raw, wacc_general=args.wacc)

    QG.run_gate(analysis, today=args.today)

    json_path = os.path.join(args.outdir, f"{symbol}_analysis.json")
    dump_json(analysis, json_path)

    import render_html as RH
    import render_xlsx as RX
    html_path = os.path.join(args.outdir, f"{symbol}_dashboard.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(RH.render(analysis))
    xlsx_path = os.path.join(args.outdir, f"{symbol}_workbook.xlsx")
    RX.render(analysis, xlsx_path)

    print(json_path)
    print(html_path)
    print(xlsx_path)
    qr = analysis["quality_report"]
    print(f"[gate] degraded={qr['degraded']} critical={len(qr['critical'])} warning={len(qr['warning'])}")


if __name__ == "__main__":
    main()
