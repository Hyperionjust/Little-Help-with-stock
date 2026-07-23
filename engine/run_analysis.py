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
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402  路径引导，见 engine/_paths.py
from _util import load_json, dump_json  # noqa: E402
import resolve_symbol as R  # noqa: E402
import fetch_data as FD  # noqa: E402
import compute_metrics as CM  # noqa: E402
import quality_gate as QG  # noqa: E402
try:
    from validate import ContractUnavailable as _V_UNAVAILABLE  # noqa: E402
except ImportError:  # 契约校验器不可用时降级为 AMBER，绝不静默通过
    _V_UNAVAILABLE = ImportError


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
    ap.add_argument("--allow-contract-violation", action="store_true",
                    help="契约不通过时仍然写盘（仅供调试，正式流程不得使用）")
    ap.add_argument("--with-peers", action="store_true",
                    help="圈定可比公司并填 analysis.peers（需联网逐个取同业，速览/研报模式用）")
    ap.add_argument("--peers", help="手工指定同业代码，逗号分隔（覆盖自动圈定）")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    # 运行清单（方案 §12.3）：一次运行的完整可复现记录
    from diagnostics import RunManifest, diag
    mf = RunManifest(args.query, args.outdir)

    if args.offline_fixture:
        raw = load_json(args.offline_fixture)
        resolved = raw.get("resolution")
    else:
        token = strip_command_words(args.query)
        # 取第一个像代码/名称的 token
        m = re.search(r"[A-Za-z]{1,5}|\d{4,6}|[一-龥]{2,6}", token)
        resolved = R.resolve(m.group(0) if m else token)
        raw = FD.fetch_live(resolved)

    mf.data["resolution"] = resolved
    mf.data["chain"]["field_sources"] = raw.get("field_sources")
    mf.data["chain"]["data_gaps"] = raw.get("data_gaps")
    mf.data["chain"]["financials_source"] = (raw.get("financials") or {}).get("source")
    mf.data["chain"]["mixed_source_periods"] = (raw.get("financials") or {}).get("mixed_source_periods")
    mf.stage_done("fetch")

    symbol = resolved.get("symbol", "out")
    analysis = CM.compute(raw)

    # 三表历史标准化（契约 statements 块，阶段1-3）
    import statements as ST
    analysis["statements"] = ST.build_statements(raw)

    # 可比公司（契约 peers 块，阶段3）——按需，默认不跑（需联网逐个取同业）
    if args.with_peers or args.peers:
        try:
            import peers as PR
            def _tgt_mult():
                v = analysis.get("valuation", {}); p = analysis.get("profitability", {})
                g = analysis.get("growth", {}); q = analysis.get("quote", {})
                gv = lambda n: n.get("value") if isinstance(n, dict) else n
                return {"pe_ttm": gv(v.get("pe_ttm")), "pb": gv(v.get("pb")),
                        "ps_ttm": gv(v.get("ps_ttm")), "ev_ebitda": gv(v.get("ev_ebitda")),
                        "market_cap": gv(q.get("market_cap")), "name": resolved.get("name"),
                        "revenue_cagr_3y": gv(g.get("revenue_cagr_3y")),
                        "net_margin": gv(p.get("net_margin"))}
            analysis["peers"] = PR.build_peers(resolved, target_multiples=_tgt_mult(),
                                               manual=args.peers)
        except Exception as _e:
            analysis["peers"] = {"available": False,
                                 "_note": f"可比公司圈定失败: {str(_e)[:80]}"}
    mf.stage_done("statements_peers")
    analysis["meta"]["_raw_financials"] = raw.get("financials", {}).get("source")

    if resolved.get("is_pharma"):
        import pharma_valuation as PV
        analysis["pharma"] = PV.compute_pharma(raw, wacc_general=args.wacc)

    QG.run_gate(analysis, today=args.today)
    mf.stage_done("compute_and_gate")
    qr0 = analysis["quality_report"]
    mf.data["gates"]["quality_gate"] = {"degraded": qr0["degraded"],
                                        "critical": [c["check"] for c in qr0["critical"]],
                                        "warning": [w["check"] for w in qr0["warning"]]}
    # 门禁结果 → 结构化诊断（user_action 供引导层在失败路径上使用）
    _GATE2CODE = {"missing_core_field": "ALL_PROVIDERS_EMPTY",
                  "cross_source_price_mismatch": "CROSS_SOURCE_PRICE_MISMATCH",
                  "unit_dimension_mismatch": "UNIT_DIMENSION_MISMATCH",
                  "stale_financials": "STALE_FINANCIALS",
                  "pharma_double_penalty": "PHARMA_DOUBLE_PENALTY"}
    for c in qr0["critical"]:
        code = _GATE2CODE.get(c["check"])
        if code:
            mf.add_diag(diag(code, stage="quality_gate",
                             evidence={"gate_message": c["message"]}))
    analysis["diagnostics"] = mf.data["diagnostics"]

    json_path = os.path.join(args.outdir, f"{symbol}_analysis.json")
    dump_json(analysis, json_path)

    # ── 闸1：契约校验（方案 §3.4）────────────────────────────────
    # engine 产出后、analyst 消费前。契约不通过说明引擎产出了不合规结构，
    # 这类问题必须当场暴露——下游拿着畸形 JSON 只会把错误传播得更远。
    try:
        import validate as _V
        _errs = _V.validate_analysis(analysis)
        if _errs:
            print(f"[contract] FAIL {len(_errs)} 处不符合 analysis.schema.json：", file=sys.stderr)
            for _e in _errs[:10]:
                print(f"    {_e}", file=sys.stderr)
            if not args.allow_contract_violation:
                sys.exit(3)
        else:
            print("[contract] OK analysis.json 通过契约校验")
    except _V_UNAVAILABLE as _e:  # noqa
        print(f"[contract] AMBER 校验被跳过：{_e}", file=sys.stderr)

    import render_html as RH
    import render_xlsx as RX
    html_path = os.path.join(args.outdir, f"{symbol}_dashboard.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(RH.render(analysis))
    xlsx_path = os.path.join(args.outdir, f"{symbol}_workbook.xlsx")
    RX.render(analysis, xlsx_path)

    mf.data["env"]["engine_schema_version"] = analysis.get("schema_version")
    mf.record_artifact("analysis_json", json_path)
    mf.record_artifact("dashboard_html", html_path)
    mf.record_artifact("workbook_xlsx", xlsx_path)
    mf.stage_done("render")
    manifest_path = mf.write()

    print(json_path)
    print(html_path)
    print(xlsx_path)
    print(manifest_path)
    qr = analysis["quality_report"]
    print(f"[gate] degraded={qr['degraded']} critical={len(qr['critical'])} warning={len(qr['warning'])}")


if __name__ == "__main__":
    main()
