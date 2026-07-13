"""质量门禁：报告输出前机械自查。critical 不通过 → degraded=true，只出降级报告。

规则清单见 references/metrics-formulas.md 末节。用法：
python quality_gate.py analysis.json  (原地写回 quality_report)
"""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import load_json, dump_json  # noqa: E402


def _val(node):
    return node.get("value") if isinstance(node, dict) else node


def run_gate(analysis, today=None):
    critical, warning = [], []
    today = today or datetime.now(timezone.utc).date().isoformat()

    q = analysis.get("quote", {})
    val = analysis.get("valuation", {})

    # C1 核心字段缺失
    core = {"现价": _val(q.get("price")), "市值": _val(q.get("market_cap")),
            "PE-TTM": _val(val.get("pe_ttm"))}
    missing = [k for k, v in core.items() if v is None]
    if missing:
        critical.append({"check": "missing_core_field",
                         "message": f"核心字段缺失: {', '.join(missing)}"})

    # C2 跨源价格差 >1%
    xs = q.get("cross_source_check", {})
    if xs.get("passed") is False:
        critical.append({"check": "cross_source_price_mismatch",
                         "message": f"跨源收盘价相对差 {xs.get('max_rel_diff'):.2%} >1%: {xs.get('sources')}"})

    # C3 财报陈旧 >12 个月
    pe = val.get("pe_ttm", {})
    asof = pe.get("as_of") if isinstance(pe, dict) else None
    if asof:
        try:
            d = datetime.fromisoformat(str(asof)[:10]).date()
            days = (datetime.fromisoformat(today).date() - d).days
            if days > 365:
                critical.append({"check": "stale_financials",
                                 "message": f"TTM财报报告期 {asof} 距今 {days} 天 (>12个月)"})
        except (ValueError, TypeError):
            pass

    # 医药专项
    ph = analysis.get("pharma")
    if ph:
        dp = ph.get("double_penalty_check", {})
        if dp.get("passed") is False or not dp.get("clinical_rate_is_independent", True):
            critical.append({"check": "pharma_double_penalty",
                             "message": f"rNPV折现率({dp.get('clinical_rate')}) 等于通用WACC({dp.get('wacc')})，双重计罚"})
        # C5 早期资产累积 PoS >30%
        for a in ph.get("rnpv", {}).get("assets", []):
            phase = str(a.get("current_phase", "")).lower()
            pos = _val(a.get("cumulative_pos"))
            early = ("preclin" in phase) or ("phase1" in phase) or ("phase 1" in phase)
            if early and pos is not None and pos > 0.30:
                critical.append({"check": "pharma_pos_implausible",
                                 "message": f"{a.get('asset')} 早期资产累积PoS {pos:.1%} >30%，疑阶段判定/连乘错误"})
        # W4 user_assumption 未进核对清单
        checklist_items = {c.get("item") for c in ph.get("human_verification_checklist", [])}
        for a in ph.get("rnpv", {}).get("assets", []):
            for fld, label in [("penetration", "渗透率"), ("peak_sales", "峰值销售")]:
                node = a.get(fld, {})
                if isinstance(node, dict) and node.get("source_type") == "user_assumption":
                    key = f"{a.get('asset')} {label}"
                    if key not in checklist_items:
                        warning.append({"check": "uncovered_user_assumption",
                                        "message": f"user_assumption 未进核对清单: {key}"})
        # W5 判为临床 biotech 但管线空
        if ph.get("paradigm") == "clinical_biotech":
            if not ph.get("pipeline", {}).get("trials"):
                warning.append({"check": "empty_pipeline",
                                "message": "判为临床biotech但管线数据为空，数据获取可能失败"})

    # W1 字段覆盖率 <80%
    cov = _field_coverage(analysis)
    if cov is not None and cov < 0.80:
        warning.append({"check": "low_field_coverage",
                        "message": f"字段覆盖率 {cov:.0%} <80%"})

    # W3 技术指标未复权
    if analysis.get("meta", {}).get("adjust_mode") in ("none", "unknown"):
        warning.append({"check": "unadjusted_technicals",
                        "message": f"技术指标基于 {analysis['meta'].get('adjust_mode')} 数据(非前复权)"})

    # W2 PE/PB 异常跳变
    for k in ("pe_ttm", "pb"):
        node = val.get(k, {})
        prev = node.get("prev_value") if isinstance(node, dict) else None
        cur = _val(node)
        if prev and cur and prev > 0 and (cur / prev > 5 or cur / prev < 0.2):
            warning.append({"check": "pe_pb_outlier",
                            "message": f"{k} 从 {prev} 跳到 {cur}，疑未处理非经常损益或除权"})

    degraded = len(critical) > 0
    analysis["quality_report"] = {
        "passed": len(critical) == 0 and len(warning) == 0,
        "degraded": degraded,
        "critical": critical,
        "warning": warning,
        "field_coverage": cov,
    }
    return analysis


def _field_coverage(analysis):
    """统计关键块里 value 非 None 的比例。"""
    total, filled = 0, 0
    for block in ("valuation", "profitability", "solvency", "growth"):
        b = analysis.get(block, {})
        for k, node in b.items():
            if isinstance(node, dict) and "value" in node:
                total += 1
                if node["value"] is not None:
                    filled += 1
    return (filled / total) if total else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("--today")
    args = ap.parse_args()
    analysis = load_json(args.analysis)
    run_gate(analysis, today=args.today)
    dump_json(analysis, args.analysis)
    qr = analysis["quality_report"]
    print(f"passed={qr['passed']} degraded={qr['degraded']} "
          f"critical={len(qr['critical'])} warning={len(qr['warning'])}")
    sys.exit(2 if qr["degraded"] else 0)


if __name__ == "__main__":
    main()
