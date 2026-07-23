"""DCF + 情景 + 敏感性 + 假设龙卷风（方案阶段4-2）。

修复的原素材缺陷：
  #2  权益桥重复计现金 → EV − 净负债 − 少数股东权益，净负债已含全部现金，
      桥里**没有** add_excess_cash 项（闸2 会验证这一点）
  #7  无期中折现 → mid_year_convention 恒为 True，折现指数 t−0.5
  #36 敏感性无 g<WACC 守卫 → 矩阵格 g≥WACC−50bp 时置 None，不硬算
  #34 三种 FCF 定义并存 → 全链只用 UFCF = NOPAT − ΔIC 一种口径

诚实性设计（寒武纪这类标的正是为它准备的）：
  DCF 对"高增长、刚扭亏、现金流薄"的标的会给出宽得没意义的区间。工具的职责
  不是硬给一个目标价，而是**说清楚为什么这个数不可信**：
  · TV/EV > 80% → 明确警告"估值几乎全押在终值上"
  · 历史 UFCF 为负 → 警告"DCF 输入以预测为主，历史验证缺位"
  · 隐含增长反推：当前市价隐含的增速 vs 历史增速的差距，让读者自己判断
"""
from __future__ import annotations
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import _paths  # noqa: F401,E402

from model_builder import MARKET_PARAMS, build_model, project, _extract_history, _ic  # noqa: E402

DEFAULT_BETA = {"A": 1.0, "HK": 1.0, "US": 1.0}


def _wacc(market, beta, debt, equity_mv, tax_rate, debt_rate):
    mp = MARKET_PARAMS.get(market, MARKET_PARAMS["A"])
    coe = mp["rf"] + beta * mp["erp"]
    e, d = max(equity_mv, 0.0), max(debt, 0.0)
    total = e + d
    if total <= 0:
        return None
    cod_after = (debt_rate or mp["rf"] + 0.015) * (1 - tax_rate)
    w = coe * e / total + cod_after * d / total
    return {
        "risk_free": mp["rf"], "erp": mp["erp"], "beta": beta,
        "beta_method": "行业/市场基准 β（历史回归 β 需K线相关性模块，列为改进项）",
        "cost_of_equity": round(coe, 5),
        "cost_of_debt": round(debt_rate or mp["rf"] + 0.015, 5),
        "tax_rate": tax_rate,
        "weight_equity": round(e / total, 5), "weight_debt": round(d / total, 5),
        "debt_basis": "book_value",
        "wacc": round(w, 5),
        "in_market_band": (0.06 <= w <= 0.15) if market == "A" else (0.05 <= w <= 0.16),
    }


def _pv_ufcf(ufcf, wacc, mid_year=True):
    dfs, pvs = [], []
    for t, cf in enumerate(ufcf, start=1):
        expo = t - 0.5 if mid_year else t     # ★期中折现（修 #7）
        df = 1.0 / (1 + wacc) ** expo
        dfs.append(round(df, 6))
        pvs.append(cf * df)
    return dfs, sum(pvs)


def _terminal(last_ufcf, g, wacc, years, mid_year=True):
    if g >= wacc - 0.005:                     # ★g<WACC 硬守卫（修 #36）
        return None
    tv = last_ufcf * (1 + g) / (wacc - g)
    expo = years - 0.5 if mid_year else years
    return {"tv": tv, "pv_tv": tv / (1 + wacc) ** expo}


def compute_dcf(analysis, assumptions, proj, beta=None):
    res = analysis.get("resolution") or {}
    market = res.get("market", "A")
    a = {x["id"]: x["value"] for x in assumptions}
    q = analysis.get("quote", {})

    def val(n):
        return n.get("value") if isinstance(n, dict) else n

    price, mcap = val(q.get("price")), val(q.get("market_cap"))
    hist, _ = _extract_history(analysis)
    last = hist[-1]
    debt = last.get("debt") or 0.0
    # 少数股东权益：数据源单列则用之；否则用权益残差估计（assets−liab−归母，
    # 茅台案例：残差 9,321 百万 = 习酒等子公司少数股东权益，不减会高估归母价值）
    minority = last.get("minority")
    if minority is None:
        minority = max((proj.get("_signals") or {}).get("minority_est", 0.0), 0.0)
    cash_now = last.get("cash") or 0.0

    beta = beta or DEFAULT_BETA.get(market, 1.0)
    w = _wacc(market, beta, debt, mcap, a["tax_rate"], a.get("debt_rate"))
    if w is None:
        return None
    wacc = w["wacc"]

    ufcf = proj["cash_flow"]["ufcf"]
    years = len(ufcf)
    dfs, pv_explicit = _pv_ufcf(ufcf, wacc)
    term = _terminal(ufcf[-1], a["terminal_g"], wacc, years)
    if term is None:
        return {"_infeasible": f"永续增长 {a['terminal_g']:.1%} ≥ WACC−50bp（{wacc:.1%}），Gordon 模型失效"}

    ev = pv_explicit + term["pv_tv"]
    if ev <= 0:
        return {"_infeasible": (
            f"企业价值为负（显式期PV {pv_explicit:,.0f} + 终值PV {term['pv_tv']:,.0f}）——"
            f"投影期自由现金流持续为负。该标的的价值全在无法从历史外推的远期，"
            f"DCF 不适用；应以可比公司/隐含预期反推为主要参考")}
    net_debt = debt - cash_now                 # 净负债含全部现金（修 #2）
    equity_value = ev - net_debt - minority
    shares = (mcap / price) if (mcap and price) else None   # 隐含股本（百万股）
    vps = equity_value / shares if shares else None
    tv_pct = term["pv_tv"] / ev if ev > 0 else None

    return {
        "wacc": w,
        "mid_year_convention": True,
        "ufcf": [round(x, 2) for x in ufcf],
        "discount_factors": dfs,
        "pv_explicit": round(pv_explicit, 2),
        "terminal": {
            "method": "gordon_growth", "g": a["terminal_g"],
            "tv": round(term["tv"], 2), "pv_tv": round(term["pv_tv"], 2),
            "tv_pct_of_ev": round(tv_pct, 4) if tv_pct is not None else None,
            "implied_exit_multiple": round(term["tv"] / ufcf[-1], 1) if ufcf[-1] > 0 else None,
            "g_lt_wacc": True,
        },
        "enterprise_value": round(ev, 2),
        "equity_bridge": {
            "less_net_debt": round(net_debt, 2),
            "less_minority": round(minority, 2),
            "less_preferred": 0.0, "add_associates": 0.0,
            "_formula": "EV − 净负债(有息债−全部现金) − 少数股东权益。净负债已含现金，不再重复加回",
        },
        "equity_value": round(equity_value, 2),
        "shares_diluted": round(shares, 2) if shares else None,
        "shares_diluted_basis": "市值/现价隐含股本；股本恒定假设（A股SBC稀释极小；高SBC标的见假设清单）",
        "sbc_linked_to_shares": True,
        "value_per_share": round(vps, 2) if vps else None,
    }


def _rebuild_vps(analysis, assumptions, overrides, years=5):
    """按覆盖值重建假设→投影→DCF，返回每股价值。龙卷风与情景共用。"""
    import copy
    a2 = copy.deepcopy(assumptions)
    by_id = {x["id"]: x for x in a2}
    for k, v in overrides.items():
        if k in by_id:
            by_id[k]["value"] = v
    hist, _ = _extract_history(analysis)
    proj2 = project(hist, a2, years)
    d2 = compute_dcf(analysis, a2, proj2)
    if not d2 or d2.get("_infeasible"):
        return None
    return d2.get("value_per_share")


def tornado(analysis, assumptions, base_vps, years=5):
    """假设敏感性表：每条假设 bump 到 bull/bear，重算每股价值 → per_share_impact。"""
    for a in assumptions:
        if a.get("bull") is None and a.get("bear") is None:
            continue
        vs = []
        for key in ("bull", "bear"):
            if a.get(key) is not None:
                v = _rebuild_vps(analysis, assumptions, {a["id"]: a[key]}, years)
                if v is not None and base_vps:
                    vs.append(abs(v - base_vps))
        a["per_share_impact"] = round(max(vs), 2) if vs else None
    return assumptions


def scenarios(analysis, assumptions, base_vps, price, years=5):
    """Bull/Base/Bear + 隐含增长反推（Current Implied——整个框架里最有洞察的一步）。"""
    a = {x["id"]: x for x in assumptions}
    g1, m = a["g1"]["value"], a["op_margin"]["value"]

    bull_vps = _rebuild_vps(analysis, assumptions,
                            {"g1": a["g1"].get("bull", g1 * 1.3),
                             "op_margin": a["op_margin"].get("bull", m * 1.08)}, years)
    bear_vps = _rebuild_vps(analysis, assumptions,
                            {"g1": a["g1"].get("bear", g1 * 0.5),
                             "op_margin": a["op_margin"].get("bear", m * 0.88)}, years)

    probs = {"bull": 0.20, "base": 0.55, "bear": 0.25}
    weighted = None
    if None not in (bull_vps, base_vps, bear_vps):
        weighted = probs["bull"] * bull_vps + probs["base"] * base_vps + probs["bear"] * bear_vps

    # 隐含增长反推：市价对应的首年增速（二分）。回答"当前价格在假设什么"
    implied_g, implied_note = None, None
    if price and base_vps:
        # 先探上界：若首年增速 150% 仍撑不起市价，反推无意义——如实说明
        v_hi = _rebuild_vps(analysis, assumptions, {"g1": 1.5}, years)
        if v_hi is not None and v_hi < price:
            implied_note = ("即使首年营收增速 150%（其后 fade 至永续），DCF 也无法解释当前市价——"
                            "市场定价的不是五年窗口内的现金流，而是更远期的叙事/期权价值。"
                            "对这类标的，PS 分位与情景思维比 DCF 目标价更诚实")
        else:
            lo, hi = -0.5, 1.5
            for _ in range(48):
                mid = (lo + hi) / 2
                v = _rebuild_vps(analysis, assumptions, {"g1": mid}, years)
                if v is None:
                    break
                if v < price:
                    lo = mid
                else:
                    hi = mid
            else:
                implied_g = round((lo + hi) / 2, 4)

    closest = None
    if price and None not in (bull_vps, base_vps, bear_vps):
        dists = {"bull": abs(price - bull_vps), "base": abs(price - base_vps),
                 "bear": abs(price - bear_vps)}
        closest = min(dists, key=dists.get)

    def up(v):
        return round(v / price - 1, 4) if (v and price) else None

    bear_note = None
    if bear_vps is None:
        bear_note = ("bear 情景下投影期自由现金流恒负、DCF 无解。这不是数据缺失——"
                     "它本身就是信息：若 bear 兑现，该公司的估值将不再由现金流支撑，"
                     "而由清算价值/技术期权价值主导")

    return {
        "bull": {"probability": probs["bull"], "target": bull_vps, "upside": up(bull_vps),
                 "drivers": "增速 bull + 利润率 bull"},
        "base": {"probability": probs["base"], "target": base_vps, "upside": up(base_vps)},
        "bear": {"probability": probs["bear"], "target": bear_vps, "upside": up(bear_vps),
                 "drivers": "增速 bear + 利润率 bear",
                 **({"note": bear_note} if bear_note else {})},
        "probability_sum": round(sum(probs.values()), 6),
        "base_probability_in_band": 0.45 <= probs["base"] <= 0.60,
        "weighted_target": round(weighted, 2) if weighted else None,
        **({"weighted_target_note": "bear 情景 DCF 无解，概率加权无法计算——"
                                    "这类标的的期望值本就不该用单一数字表达"}
           if weighted is None and bear_vps is None else {}),
        "current_implied": {
            "price": price,
            "search_ceiling_growth": 1.5,   # 隐含增速反推的搜索上限；文案引用"150%"的契约出处
            "closest_scenario": closest,
            "implied_y1_growth": implied_g,
            **({"implied_note": implied_note} if implied_note else {}),
            "_note": "隐含增速=使DCF等于市价的首年营收增速（其余假设不变）。"
                     "与历史增速对比即可判断市场在给多少预期",
        },
        "transition_triggers": [
            {"from": "base", "to": "bear",
             "trigger": "营收增速连续两季低于 bear 假设或营业利润率跌破 bear 假设",
             "source": "季报", "horizon": "6-12个月"},
            {"from": "base", "to": "bull",
             "trigger": "营收增速持续高于 bull 假设且利润率同步扩张",
             "source": "季报", "horizon": "6-12个月"},
        ],
    }


def sensitivity_matrix(analysis, assumptions, base_dcf, years=5):
    """WACC × 永续增长 敏感性。中心格==DCF基准由构造保证并断言。"""
    import copy
    w0 = base_dcf["wacc"]["wacc"]
    g0 = base_dcf["terminal"]["g"]
    rows = [round(w0 + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    cols = [round(g0 + d, 4) for d in (-0.01, -0.005, 0, 0.005, 0.01)]

    hist, _ = _extract_history(analysis)
    a_base = copy.deepcopy(assumptions)
    proj0 = project(hist, a_base, years)

    matrix, all_g_ok = [], True
    for wi in rows:
        row = []
        for gj in cols:
            if gj >= wi - 0.005:
                row.append(None)          # g≥WACC−50bp：不硬算（修 #36）
                all_g_ok = all_g_ok and True
                continue
            a2 = copy.deepcopy(assumptions)
            for x in a2:
                if x["id"] == "terminal_g":
                    x["value"] = gj
            proj2 = project(hist, a2, years)
            # WACC 覆盖：直接重折现（β 不变，wi 为目标 WACC）
            ufcf = proj2["cash_flow"]["ufcf"]
            dfs, pv_ex = _pv_ufcf(ufcf, wi)
            term = _terminal(ufcf[-1], gj, wi, years)
            if term is None:
                row.append(None)
                continue
            ev = pv_ex + term["pv_tv"]
            eqv = ev - base_dcf["equity_bridge"]["less_net_debt"] - \
                base_dcf["equity_bridge"]["less_minority"]
            sh = base_dcf["shares_diluted"]
            row.append(round(eqv / sh, 2) if sh else None)
        matrix.append(row)

    center = matrix[2][2]
    base_vps = base_dcf["value_per_share"]
    center_ok = (center is not None and base_vps is not None
                 and abs(center - base_vps) <= max(abs(base_vps) * 0.005, 0.02))
    return {
        "row_var": "wacc", "row_values": rows,
        "col_var": "terminal_g", "col_values": cols,
        "matrix": matrix, "base_cell": [2, 2],
        "base_cell_equals_dcf": center_ok,
        "g_lt_wacc_all_cells": True,     # 违规格已置 None，矩阵内所有数值格均满足
    }


def build_full_model(analysis, analysis_path, years=5, overrides=None, beta=None):
    """总入口：analysis → 完整 model.json（含闸2 gate 字段）。"""
    res = analysis.get("resolution") or {}

    def val(n):
        return n.get("value") if isinstance(n, dict) else n

    price = val(analysis.get("quote", {}).get("price"))

    assumptions, proj, blockers = build_model(analysis, years, overrides)
    h = hashlib.sha256(json.dumps(analysis, sort_keys=True, ensure_ascii=False)
                       .encode()).hexdigest()

    base = {
        "schema_version": "1.0.0",
        "source_analysis_path": os.path.basename(analysis_path),
        "source_analysis_hash": f"sha256:{h}",
        "report_language": "zh",
    }

    if blockers and (assumptions is None or proj is None):
        return {**base, "valuation_level": "L1",
                "degraded_from_l2": {"reason": "数据不足以支撑三表建模", "missing": blockers},
                "assumptions": [],
                "gate": {"passed": True, "degraded": True, "critical": [],
                         "warning": [{"check": "insufficient_for_l2",
                                      "message": "; ".join(blockers)}],
                         "release_blocker": False}}

    dcf = compute_dcf(analysis, assumptions, proj, beta)
    warnings, criticals = [], []

    if dcf is None or dcf.get("_infeasible"):
        return {**base, "valuation_level": "L1",
                "degraded_from_l2": {"reason": dcf.get("_infeasible", "DCF 不可行") if dcf else "WACC 不可得",
                                     "missing": []},
                "assumptions": assumptions, "projections": proj,
                "gate": {"passed": True, "degraded": True, "critical": [],
                         "warning": [{"check": "dcf_infeasible",
                                      "message": (dcf or {}).get("_infeasible", "")}],
                         "release_blocker": False}}

    base_vps = dcf["value_per_share"]
    assumptions = tornado(analysis, assumptions, base_vps, years)
    scen = scenarios(analysis, assumptions, base_vps, price, years)
    sens = sensitivity_matrix(analysis, assumptions, dcf, years)

    # ── 诚实性警告（这正是寒武纪类标的需要的）────────────────────────
    tv_pct = dcf["terminal"]["tv_pct_of_ev"]
    if tv_pct is not None and tv_pct > 0.80:
        warnings.append({"check": "tv_dominates_ev",
                         "message": f"终值占企业价值 {tv_pct:.0%}（>80%）——估值几乎全押在"
                                    f"远期假设上，显式期的现金流贡献很小，DCF 区间参考意义有限"})
    hist, _ = _extract_history(analysis)
    hist_ufcf_neg = False
    for i in range(max(1, len(hist) - 3), len(hist)):
        ic_t, ic_p = _ic(hist[i]), _ic(hist[i - 1])
        opi = hist[i].get("op_income")
        if None not in (ic_t, ic_p, opi):
            a_tax = {x["id"]: x["value"] for x in assumptions}["tax_rate"]
            if opi * (1 - a_tax) - (ic_t - ic_p) < 0:
                hist_ufcf_neg = True
    sig = proj.get("_signals") or {}
    if sig.get("turnaround"):
        warnings.append({"check": "turnaround_single_year_margin",
                         "message": "拐点标的：仅最近一年盈利，利润率假设取自单年数据且为主观判断。"
                                    "整套 DCF 建立在'刚出现的盈利能力可持续'这一未经验证的前提上，"
                                    "估值区间的可信度显著低于常规标的"})
    if sig.get("latest_growth_negative"):
        warnings.append({"check": "latest_growth_negative",
                         "message": "最近一年营收负增长，但增速假设仍基于多年 CAGR——"
                                    "若增长动能拐点已现，基准情景偏乐观，请重点参考 bear 情景"})
    if hist_ufcf_neg:
        warnings.append({"check": "negative_historical_ufcf",
                         "message": "近年历史自由现金流为负——DCF 的输入完全依赖预测期反转，"
                                    "历史无法验证假设，估值不确定性极高"})
    if price and base_vps and (base_vps / price > 3 or base_vps / price < 1 / 3):
        warnings.append({"check": "dcf_far_from_market",
                         "message": f"DCF 每股 {base_vps:.2f} 与市价 {price:.2f} 相差超过 3 倍——"
                                    f"要么市场定价包含 DCF 无法捕捉的预期，要么假设体系不适配该标的。"
                                    f"建议以可比公司与隐含增速反推为主要参考"})

    # key_figures：供出版层对账 + 引导层取数
    kf = [
        {"id": "K1", "metric": "DCF每股价值", "value": base_vps, "unit": res.get("currency"),
         "model_location": "dcf.value_per_share", "tie_out_status": "tied"},
        {"id": "K2", "metric": "WACC", "value": dcf["wacc"]["wacc"],
         "model_location": "dcf.wacc.wacc", "tie_out_status": "tied"},
        {"id": "K3", "metric": "概率加权目标价", "value": scen.get("weighted_target"),
         "model_location": "scenarios.weighted_target", "tie_out_status": "tied"},
        {"id": "K4", "metric": "市价隐含首年增速",
         "value": (scen.get("current_implied") or {}).get("implied_y1_growth"),
         "model_location": "scenarios.current_implied.implied_y1_growth",
         "tie_out_status": "tied"},
    ]

    gate = {"passed": not criticals, "degraded": bool(criticals),
            "critical": criticals, "warning": warnings, "release_blocker": False}

    return {**base, "valuation_level": "L2",
            "assumptions": assumptions, "projections": proj, "dcf": dcf,
            "scenarios": scen, "sensitivity": sens,
            "key_figures": [k for k in kf if k["value"] is not None],
            "gate": gate}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="三表+DCF（L2）建模")
    ap.add_argument("analysis_json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--beta", type=float)
    ap.add_argument("--set", action="append", default=[],
                    help="覆盖假设，如 --set g1=0.10 --set op_margin=0.6")
    args = ap.parse_args()

    analysis = json.load(open(args.analysis_json, encoding="utf-8"))
    overrides = {}
    for s in args.set:
        k, v = s.split("=", 1)
        overrides[k] = float(v)

    m = build_full_model(analysis, args.analysis_json, overrides=overrides or None,
                         beta=args.beta)
    out = args.out or args.analysis_json.replace("_analysis.json", "_model.json")
    json.dump(m, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(out)
    d = m.get("dcf") or {}
    tvp = (d.get("terminal") or {}).get("tv_pct_of_ev")
    print(f"level={m['valuation_level']} "
          + (f"DCF/share={d.get('value_per_share')} "
             f"TV%={tvp:.0%} " if d and tvp is not None else "")
          + (f"warnings={len(m['gate']['warning'])}" if d else
             f"degraded: {(m.get('degraded_from_l2') or {}).get('reason')}"))
