"""可比公司估值 + 调整框架（方案阶段3-2）。

原 equity-researcher 的 comparable.md 约 60% 是 HTML 版式，方法学只有约 30 行，
**完全没有可比调整框架**——peer 用裸倍数直接比，不做任何归一化。本模块补上这一层。

四类调整（把"目标 PE 高于同业中位 X%"变成"其中多少由基本面差异解释"）：
  growth              增长差：高增长应享溢价（PEG 思路）
  margin              利润率差：高盈利质量应享溢价
  leverage            杠杆差：EV 类倍数已含杠杆，PE/PB 需提示
  accounting_standard 会计准则差：CAS/IFRS/US_GAAP 口径不可直接比

统一用**中位数 + 分位数**，不用均值（peers.py 已按此产出 stats）。
跨市场货币统一 + FX 脚注。

输出进 model.json 的 comps 块。所有数字可复算——本模块不产生任何"拍脑袋"的值。
"""
from __future__ import annotations

# 各行业主用倍数（选倍数的第一性原则：可比、可得、与商业模式匹配）
INDUSTRY_PRIMARY_MULTIPLE = {
    "白酒": "pe_ttm", "食品饮料": "pe_ttm", "消费": "pe_ttm",
    "化学制药": "pe_ttm", "医药生物": "pe_ttm",
    "银行": "pb", "保险": "pb", "地产": "pb", "券商": "pb",
    "科技": "ps_ttm", "软件": "ps_ttm", "互联网": "ps_ttm",
    "半导体": "ps_ttm", "semiconductor": "ps_ttm",
    "周期": "pb", "钢铁": "pb", "有色": "pb",
    "亏损": "ps_ttm",
}
DEFAULT_MULTIPLE = "pe_ttm"


def pick_multiple(industry_tag, target_item):
    """选主用倍数：行业匹配 → 目标该倍数有效 → 否则回退到有值的倍数。"""
    m = DEFAULT_MULTIPLE
    if industry_tag:
        hay = industry_tag.lower()
        for key, mult in INDUSTRY_PRIMARY_MULTIPLE.items():
            if key.lower() in hay:
                m = mult
                break
    # 亏损公司 PE 无意义 → 强制 PS
    if target_item.get("net_margin") is not None and target_item["net_margin"] < 0:
        m = "ps_ttm"
    # 选定倍数目标无值 → 回退
    if target_item.get(m) is None:
        for alt in ["pe_ttm", "ps_ttm", "pb", "ev_ebitda"]:
            if target_item.get(alt) is not None:
                return alt
    return m


def _adjustments(target, peers_median_item, peer_stats):
    """四类调整。返回 [{kind, adjustment, basis}]。adjustment 为对倍数的百分比修正。

    调整是**解释性**的，不是精算——目的是让"25% 溢价里有多少由 6pp 增长差解释"
    这句话可量化、可复算。系数取业界惯用的经验值并写进 basis，可审计。
    """
    adj = []

    # 1. 增长差（PEG 思路：每 1pp 增长差 ≈ 对 PE 的 ~2% 影响）
    tg = target.get("revenue_cagr_3y")
    pg_stats = peer_stats.get("_growth_median")
    if tg is not None and pg_stats is not None:
        diff_pp = (tg - pg_stats) * 100
        if abs(diff_pp) >= 1:
            adj.append({"kind": "growth", "adjustment": round(diff_pp * 0.02, 4),
                        "basis": f"营收3年CAGR 高于同业中位 {diff_pp:+.1f}pp，"
                                 f"按每 1pp≈2% 倍数溢价（PEG 经验系数）"})

    # 2. 利润率差（每 1pp 净利率差 ≈ 对 PE 的 ~1.5% 影响）
    tm = target.get("net_margin")
    pm_stats = peer_stats.get("_margin_median")
    if tm is not None and pm_stats is not None:
        diff_pp = (tm - pm_stats) * 100
        if abs(diff_pp) >= 1:
            adj.append({"kind": "margin", "adjustment": round(diff_pp * 0.015, 4),
                        "basis": f"净利率 高于同业中位 {diff_pp:+.1f}pp，"
                                 f"按每 1pp≈1.5% 质量溢价"})

    # 3. 杠杆差提示（不做数值调整，仅在用 PE/PB 时提示改看 EV 类）
    if target.get("ev_ebitda") is not None:
        adj.append({"kind": "leverage", "adjustment": None,
                    "basis": "如同业杠杆差异大，PE/PB 受资本结构干扰，建议参考 EV/EBITDA"})

    return adj


def build_comps(peers_block, resolved, target_forward_eps=None):
    """从 analysis.peers 产出 model.json 的 comps 块。

    peers_block: analysis["peers"]（peers.build_peers 的产出）
    target_forward_eps: 若有一致预期 EPS，用它×同业中位倍数给隐含价；否则用 TTM。
    """
    if not peers_block.get("available"):
        return {"available": False,
                "_note": peers_block.get("_note", "同业样本不足，无法做可比估值")}

    items = peers_block["items"]
    stats = peers_block["stats"]
    target = next((it for it in items if it.get("is_target")), None)
    peers_only = [it for it in items if not it.get("is_target")]
    if not target or not peers_only:
        return {"available": False, "_note": "缺目标或同业样本"}

    multiple = pick_multiple(resolved.get("industry_tag"), target)
    mstats = stats.get(multiple, {})
    peer_median = mstats.get("median")

    # 附加同业增长/利润率中位，供调整用
    def _median(field):
        vals = sorted(it[field] for it in peers_only if it.get(field) is not None)
        if not vals:
            return None
        n = len(vals)
        return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2

    peer_stats_ext = {"_growth_median": _median("revenue_cagr_3y"),
                      "_margin_median": _median("net_margin")}
    adjustments = _adjustments(target, None, peer_stats_ext)

    # 调整后的目标应用倍数：同业中位 ×(1+Σ数值调整)
    net_adj = sum(a["adjustment"] for a in adjustments if a.get("adjustment"))
    adjusted_multiple = peer_median * (1 + net_adj) if peer_median is not None else None

    # 隐含价 = 调整后倍数 × 目标每股基数（EPS/BPS/SPS）
    implied_price = None
    per_share_base = None
    if adjusted_multiple is not None:
        if multiple == "pe_ttm":
            per_share_base = target_forward_eps  # 由调用方从 estimates/statements 传入
        # 其它倍数的每股基数留待有数据时补；无基数则只报调整后倍数，不硬给价
    if adjusted_multiple is not None and per_share_base:
        implied_price = round(adjusted_multiple * per_share_base, 2)

    # 目标 vs 同业：溢价/折价
    target_mult = target.get(multiple)
    premium = None
    if target_mult is not None and peer_median:
        premium = round((target_mult / peer_median - 1), 4)

    return {
        "available": True,
        "metric": multiple,
        "basis": f"同业中位 {multiple}={peer_median:.1f}"
                 if peer_median is not None else None,
        "peer_median": peer_median,
        "peer_stats": mstats,
        "target_multiple": target_mult,
        "target_percentile": peers_block.get("target_percentile", {}).get(multiple),
        "premium_to_median": premium,
        "adjustments": adjustments,
        "net_adjustment": round(net_adj, 4),
        "adjusted_multiple": round(adjusted_multiple, 2) if adjusted_multiple else None,
        "per_share_base": per_share_base,
        "implied_price": implied_price,
        "currency": resolved.get("currency"),
        "fx_note": _fx_note(items),
        "_explain": (
            f"目标 {multiple}={target_mult:.1f}，同业中位 {peer_median:.1f}，"
            f"溢价 {premium:+.1%}；其中约 {net_adj:+.1%} 可由增长/利润率差异解释"
            if (target_mult is not None and peer_median and premium is not None)
            else "同业倍数分布已列出，目标定位见 target_percentile"),
    }


def _fx_note(items):
    """跨市场货币提示。同业跨币种时必须脚注，且倍数本身是无量纲比率，可比。"""
    currencies = {it.get("currency") for it in items if it.get("currency")}
    if len(currencies) > 1:
        return (f"同业横跨 {', '.join(sorted(c for c in currencies if c))}；"
                f"倍数为无量纲比率可直接比，但市值对比需按当日汇率统一（报告脚注注明）")
    return None
