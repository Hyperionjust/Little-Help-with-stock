"""可比公司圈定 + 多标的取数（方案阶段3-1）。全新能力——两个原 skill 都没有多标的路径。

职责：
  1. 圈定同业候选（行业分类 + 同市场，市值带宽由 comps 层做二次筛）
  2. 复用 fetch_data 的降级链，对每个候选取 quote+financials，算基础倍数
  3. 分位数统计**由脚本计算**（原 equity-researcher 是 LLM 手填），统一中位数+分位数

设计红线：
  · 圈不准就降级，不硬猜。行业名录命中不足时，要求用户手工指定
    （--peers 600276.SH,000538.SZ,...），并在 selection_basis 里如实说明。
  · target 自己也进 items（is_target=true），供 comps 层算目标分位。
  · stats 用中位数+分位数，不用均值——4 个样本的均值对离群值过于敏感。

用法：
  from peers import build_peers
  analysis["peers"] = build_peers(resolved, target_valuation, fetch_fn=..., manual=None)
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402
from _util import now_iso  # noqa: E402

_MULTIPLE_FIELDS = ["pe_ttm", "pb", "ps_ttm", "ev_ebitda"]


# ── 内置同业名录（离线可用的第一版，联网可由行业分类接口覆盖）────────────
# 键 = 行业标签子串（大小写不敏感），值 = 该行业的同业代码清单（含各市场后缀）。
# 名录是"够用即可"的种子——命中不足时走用户手工指定，绝不硬凑。
PEER_UNIVERSE = {
    "白酒": ["600519", "000858", "000568", "600809", "002304", "000596"],
    "食品饮料": ["600519", "000858", "603288", "600887", "002304"],
    "化学制药": ["600276", "000538", "002422", "600085", "300347", "688180"],
    "医药生物": ["600276", "000538", "300760", "603259", "002422"],
    "technology hardware": ["AAPL", "MSFT", "005930.KS", "2317.TW"],
    "semiconductor": ["NVDA", "AMD", "INTC", "TSM", "688981", "002371"],
    "白色家电": ["000651", "000333", "600690", "000921"],
    "银行": ["601398", "601288", "600036", "601988", "600000"],
}


def _match_universe(industry_tag):
    """行业标签 → 同业候选代码。返回 (codes, basis_note)。命中不足返回 ([], 说明)。"""
    if not industry_tag:
        return [], "无行业标签，无法自动圈定同业"
    hay = industry_tag.lower()
    for key, codes in PEER_UNIVERSE.items():
        if key.lower() in hay:
            return list(codes), f"内置同业名录命中「{key}」"
    return [], f"行业「{industry_tag}」不在内置名录，需手工指定同业"


def _basic_multiples(raw, resolve_fn, compute_fn):
    """对单个候选取数并算倍数。任一步失败返回 None（不阻断整体）。"""
    try:
        analysis = compute_fn(raw)
        v = analysis.get("valuation", {})
        p = analysis.get("profitability", {})
        g = analysis.get("growth", {})
        q = analysis.get("quote", {})

        def val(node):
            return node.get("value") if isinstance(node, dict) else node

        return {
            "market_cap": val(q.get("market_cap")),
            "pe_ttm": val(v.get("pe_ttm")), "pb": val(v.get("pb")),
            "ps_ttm": val(v.get("ps_ttm")), "ev_ebitda": val(v.get("ev_ebitda")),
            "revenue_cagr_3y": val(g.get("revenue_cagr_3y")),
            "net_margin": val(p.get("net_margin")),
            "_source": (raw.get("financials") or {}).get("source", "unknown"),
        }
    except Exception:
        return None


def _percentiles(values):
    """纯 Python 分位数（线性插值，与 numpy.percentile 默认一致）。无 numpy 依赖。"""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    n = len(xs)

    def pct(p):
        if n == 1:
            return xs[0]
        idx = p / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return xs[lo] * (1 - frac) + xs[hi] * frac

    return {"max": xs[-1], "p75": pct(75), "median": pct(50),
            "mean": sum(xs) / n, "p25": pct(25), "min": xs[0], "n": n}


def _target_percentile(target_val, peer_vals):
    """目标值在同业分布中的分位（0-1）。用于"贵/便宜"判断。"""
    xs = sorted(v for v in peer_vals if v is not None)
    if not xs or target_val is None:
        return None
    below = sum(1 for v in xs if v < target_val)
    return round(below / len(xs), 3)


def build_peers(resolved, target_multiples=None, *, fetch_fn=None,
                resolve_fn=None, compute_fn=None, manual=None, max_peers=8):
    """圈定并取数。

    参数：
      resolved         目标的 resolution
      target_multiples 目标自身的倍数（来自主分析，避免重复取数）
      fetch_fn/resolve_fn/compute_fn  依赖注入（便于离线测试；默认用真实模块）
      manual           用户手工指定的同业代码列表；给了就用它，不走名录
    """
    market = resolved.get("market")
    target_sym = resolved.get("symbol")

    # 1. 确定候选清单
    if manual:
        codes = [c.strip() for c in (manual if isinstance(manual, list)
                                     else str(manual).split(",")) if c.strip()]
        basis = f"用户手工指定 {len(codes)} 家同业"
    else:
        codes, basis = _match_universe(resolved.get("industry_tag"))

    # 圈不准 → 诚实降级，不硬猜
    if not codes:
        return {"available": False, "selection_basis": basis,
                "items": [], "excluded": [],
                "stats": {}, "target_percentile": {},
                "_note": "未能自动圈定同业。请手工指定：--peers 600276,000538,...",
                "_as_of": now_iso()}

    # 目标自己也进候选（去重）
    if target_sym and target_sym not in [c.split(".")[0] for c in codes]:
        codes = [target_sym] + codes

    # 2. 逐个取数（真实运行时用注入的 fetch/compute）
    if fetch_fn is None or resolve_fn is None or compute_fn is None:
        import fetch_data as FD
        import resolve_symbol as R
        import compute_metrics as CM
        fetch_fn = fetch_fn or FD.fetch_live
        resolve_fn = resolve_fn or R.resolve
        compute_fn = compute_fn or CM.compute

    items, excluded = [], []
    for code in codes[:max_peers + 1]:
        base = code.split(".")[0]
        is_target = base == str(target_sym)
        # 目标复用主分析的倍数，不重复取数
        if is_target and target_multiples:
            m = dict(target_multiples)
        else:
            try:
                res_i = resolve_fn(code)
                raw_i = fetch_fn(res_i)
                m = _basic_multiples(raw_i, resolve_fn, compute_fn)
            except Exception as e:
                excluded.append({"symbol": code, "reason": f"取数失败: {str(e)[:60]}"})
                continue
        if not m or all(m.get(f) is None for f in _MULTIPLE_FIELDS):
            excluded.append({"symbol": code, "reason": "倍数全缺，样本无效"})
            continue
        items.append({
            "symbol": code, "name": m.get("name", code), "is_target": is_target,
            "market": market, "currency": resolved.get("currency"),
            **{f: m.get(f) for f in ["market_cap"] + _MULTIPLE_FIELDS},
            "revenue_cagr_3y": m.get("revenue_cagr_3y"), "net_margin": m.get("net_margin"),
            "_source": m.get("_source"), "_as_of": now_iso(),
        })

    # 3. 分位数统计（脚本算，仅用同业、不含目标）
    peers_only = [it for it in items if not it["is_target"]]
    stats = {}
    for f in _MULTIPLE_FIELDS:
        s = _percentiles([it[f] for it in peers_only])
        if s:
            stats[f] = s

    target_pct = {}
    tgt = next((it for it in items if it["is_target"]), None)
    if tgt:
        for f in _MULTIPLE_FIELDS:
            tp = _target_percentile(tgt.get(f), [it[f] for it in peers_only])
            if tp is not None:
                target_pct[f] = tp

    return {
        "available": len(peers_only) >= 2,   # 至少 2 家同业才算有效
        "selection_basis": basis,
        "items": items, "excluded": excluded,
        "stats": stats, "target_percentile": target_pct,
        "_as_of": now_iso(),
    }


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="可比公司圈定（调试入口，需联网）")
    ap.add_argument("symbol")
    ap.add_argument("--peers", help="手工指定同业，逗号分隔")
    args = ap.parse_args()
    import resolve_symbol as R
    resolved = R.resolve(args.symbol)
    out = build_peers(resolved, manual=args.peers)
    print(json.dumps({k: v for k, v in out.items() if k != "items"},
                     ensure_ascii=False, indent=2))
    print(f"\n候选 {len(out['items'])} 家，有效同业统计: {list(out['stats'])}")
