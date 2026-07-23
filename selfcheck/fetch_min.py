"""最小取数驱动（自测/受限环境用）：行情+财报，跳过K线与基准指数。

用途：DCF/三表建模只需要 quote + financials，K线（技术面）与基准（相对强弱）
对估值无贡献却占了取数时间的大头。受限沙箱单次调用有时限，此驱动把
非必需环节显式跳过并如实记入 data_gaps——跳过不等于隐瞒。

产出与 run_analysis 同构的 analysis.json（technicals 为空、kline 缺失入 gaps），
可直接喂给 analyst/dcf.py。
"""
from __future__ import annotations
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
import _paths  # noqa: F401,E402

import resolve_symbol as R  # noqa: E402
import fetch_data as FD  # noqa: E402
import compute_metrics as CM  # noqa: E402
import statements as ST  # noqa: E402
import quality_gate as QG  # noqa: E402
from _util import dump_json, now_iso  # noqa: E402


def fetch_min(resolved):
    chain = FD.build_chain(resolved["market"])
    data_gaps = []

    quote_results, cross_prices = [], {}
    for p in chain:
        res, err = FD.with_retry(lambda p=p: p.get_quote(resolved), retries=2, base_delay=0.3)
        if err:
            data_gaps.append({"field": f"quote@{p.name}", "reason": str(err)[:120]})
        if res:
            quote_results.append((p.name, res))
            if res.get("price") is not None:
                cross_prices[p.name] = res["price"]
        if len(cross_prices) >= 2 and any(r.get("market_cap") for _, r in quote_results):
            break
    from base import QUOTE_FIELDS
    q_merged, q_src, _ = FD.merge_fields(QUOTE_FIELDS, quote_results)
    quote = dict(q_merged)
    quote["source"] = q_src.get("price", "unknown")
    quote["as_of"] = now_iso()
    quote["cross_source"] = {"prices": cross_prices}
    if not resolved.get("name"):
        for _, r in quote_results:
            if r.get("_name"):
                resolved["name"] = r["_name"]
                break

    fin_results = []
    for p in chain:
        res, err = FD.with_retry(lambda p=p: p.get_financials(resolved))
        if res and res.get("annual"):
            fin_results.append((p.name, res))
            probe = FD.merge_financials(fin_results)
            latest = probe["annual"][0] if probe["annual"] else {}
            if (len(probe["annual"]) >= 3
                    and all(latest.get(f) is not None for f in FD.CORE_FINANCIAL_FIELDS)):
                break
    financials = FD.merge_financials(fin_results) if fin_results else {"source": "none", "annual": []}

    data_gaps.append({"field": "kline", "reason": "最小驱动显式跳过（估值不需要K线）"})
    data_gaps.append({"field": "benchmark_kline", "reason": "最小驱动显式跳过"})

    return {"resolution": resolved, "quote": quote, "financials": financials,
            "kline": {"adjust": "none", "source": "skipped", "close": []},
            "benchmark_kline": {"close": []},
            "dividend": {"source": "none"}, "estimates": {"source": "none"},
            "data_gaps": data_gaps, "field_sources": q_src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--stage", choices=["fetch", "compute", "all"], default="all",
                    help="fetch=只取数存raw；compute=从raw继续；all=一步到位")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    raw_path = os.path.join(args.outdir, "raw_min.json")
    if args.stage in ("fetch", "all"):
        resolved = R.resolve(args.symbol)
        raw = fetch_min(resolved)
        dump_json(raw, raw_path)
        print(f"[fetch] {raw_path}  annual={len(raw['financials']['annual'])}期 "
              f"price={raw['quote'].get('price')}")
        if args.stage == "fetch":
            return

    raw = json.load(open(raw_path, encoding="utf-8"))
    resolved = raw["resolution"]
    analysis = CM.compute(raw)
    analysis["statements"] = ST.build_statements(raw)
    QG.run_gate(analysis)

    sys.path.insert(0, os.path.join(ROOT, "contracts"))
    try:
        import validate as V
        errs = V.validate_analysis(analysis)
        print(f"[contract] {'OK' if not errs else f'FAIL {len(errs)}处: ' + errs[0]}")
    except Exception as e:
        print(f"[contract] AMBER {e}")

    out = os.path.join(args.outdir, f"{resolved['symbol']}_analysis.json")
    dump_json(analysis, out)
    qr = analysis["quality_report"]
    cov = analysis["statements"]["coverage"]
    print(f"[out] {out}")
    print(f"[gate] degraded={qr['degraded']} critical={[c['check'] for c in qr['critical']]} "
          f"warning={[w['check'] for w in qr['warning']]}")
    print(f"[coverage] {cov['annual_years']}年年报 L2就绪={cov['sufficient_for_l2']}"
          + (f" blockers={cov.get('l2_blockers')}" if not cov['sufficient_for_l2'] else ""))


if __name__ == "__main__":
    main()
