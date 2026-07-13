"""医药/生物科技估值：rNPV / 管线 / LOE / 催化剂 / 敏感性 / 需人工核对清单。

方法学契约见 references/pharma-valuation.md。铁律：折现率用独立 clinical_discount_rate，绝不复用 WACC。
用法：python pharma_valuation.py raw_data.json analysis.json  (原地把 'pharma' 段写回 analysis)
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import av, pharma_av, load_json, dump_json, safe_div  # noqa: E402

# ---- 行业基准（benchmark，可覆盖）----
PHASE_TRANSITION_POS = {
    "preclinical_to_p1": 0.45,
    "p1_to_p2": 0.60,
    "p2_to_p3": 0.30,
    "p3_to_nda": 0.55,
    "nda_to_approval": 0.88,
}
# 各阶段之后到批准需连乘的转换序列
_STAGE_ORDER = ["preclinical", "phase1", "phase2", "phase3", "nda", "approved"]
_STAGE_TRANSITIONS = {
    "preclinical": ["preclinical_to_p1", "p1_to_p2", "p2_to_p3", "p3_to_nda", "nda_to_approval"],
    "phase1": ["p1_to_p2", "p2_to_p3", "p3_to_nda", "nda_to_approval"],
    "phase2": ["p2_to_p3", "p3_to_nda", "nda_to_approval"],
    "phase3": ["p3_to_nda", "nda_to_approval"],
    "nda": ["nda_to_approval"],
    "approved": [],
}
TA_POS_MULTIPLIER = {"oncology": 0.7, "rare_disease": 1.8, "other": 1.0}

CLINICAL_DISCOUNT_RATE_BY_PARADIGM = {
    "large_pharma_sotp": 0.10,
    "clinical_biotech": 0.125,
    "clinical_biotech_early": 0.15,
}
PENETRATION_BY_COMPETITION = {"first_in_class": 0.25, "moderate": 0.15, "crowded": 0.08}
# 峰值销售兜底默认（百万，本币）：当患者数/定价拿不到时的粗略先验，按 TA 分层，永远标 user_assumption。
# 目的是给出可编辑的 illustrative 非零 rNPV（用户在 Excel rNPV sheet 改），而非精确估值。
DEFAULT_PEAK_SALES_BY_TA = {"oncology": 3000.0, "rare_disease": 2000.0, "other": 1500.0}
LOE_DECAY = {
    "small_molecule": [0.30, 0.18, 0.12, 0.09, 0.07],   # LOE 后各年保留比例
    "biologic": [0.80, 0.68, 0.58, 0.49, 0.40],
}
RAMP_YEARS = 5  # 上市到峰值线性爬坡年数


def normalize_phase(raw_phase):
    """ClinicalTrials phase 字符串 -> 内部阶段键（取最高阶段）。"""
    if not raw_phase:
        return "phase1"
    p = str(raw_phase).lower()
    if "approv" in p or "market" in p:
        return "approved"
    if "nda" in p or "bla" in p:
        return "nda"
    if "phase4" in p or "phase 4" in p:
        return "approved"
    if "phase3" in p or "phase 3" in p:
        return "phase3"
    if "phase2" in p or "phase 2" in p:
        return "phase2"
    if "phase1" in p or "phase 1" in p or "early_phase1" in p:
        return "phase1"
    if "preclin" in p:
        return "preclinical"
    return "phase1"


def map_indication_to_ta(indication):
    s = (indication or "").lower()
    onc_kw = ["cancer", "tumor", "tumour", "carcinoma", "lymphoma", "leukemia", "leukaemia",
              "myeloma", "oncolog", "glioma", "melanoma", "sarcoma", "肿瘤", "癌", "淋巴瘤", "白血病"]
    rare_kw = ["rare", "orphan", "duchenne", "cystic fibrosis", "hemophilia", "gaucher",
               "pompe", "sma ", "amyloidosis", "罕见", "孤儿"]
    if any(k in s for k in onc_kw):
        return "oncology"
    if any(k in s for k in rare_kw):
        return "rare_disease"
    return "other"


def cumulative_pos(phase, ta="other", clamp=(0.0, 0.95)):
    """从当前阶段到批准各阶段成功率连乘 × TA 乘子。"""
    prod = 1.0
    for t in _STAGE_TRANSITIONS.get(phase, ["p1_to_p2", "p2_to_p3", "p3_to_nda", "nda_to_approval"]):
        prod *= PHASE_TRANSITION_POS[t]
    prod *= TA_POS_MULTIPLIER.get(ta, 1.0)
    lo, hi = clamp
    return max(lo, min(hi, prod))


def estimate_peak_sales(asset, ta="other"):
    """峰值销售 = 患者数 × 渗透率 × 单患者年费用 × 疗程系数。返回 (value, penetration_used, competition).

    患者数/定价缺失且未直接给 peak_sales 时 → 用 TA 分层兜底默认（illustrative，user_assumption）。
    """
    patients = asset.get("target_patients")
    price = asset.get("annual_price_per_patient")
    course = asset.get("course_factor", 1.0)
    comp = asset.get("competition", "moderate")
    pen = asset.get("penetration") or PENETRATION_BY_COMPETITION.get(comp, 0.15)
    if patients is not None and price is not None:
        return patients * pen * price * course, pen, comp
    if asset.get("peak_sales") is not None:
        return asset.get("peak_sales"), pen, comp
    # 兜底默认：给可编辑的 illustrative 非零峰值销售
    return DEFAULT_PEAK_SALES_BY_TA.get(ta, DEFAULT_PEAK_SALES_BY_TA["other"]), pen, comp


def build_revenue_curve(peak, launch_year, base_year, loe_year=None, molecule="small_molecule",
                        horizon=20):
    """返回 {t(相对base_year): revenue}。爬坡→峰值→LOE衰减。"""
    curve = {}
    if peak is None:
        return curve
    for yr in range(base_year, base_year + horizon + 1):
        t = yr - base_year
        if yr < launch_year:
            rev = 0.0
        elif yr < launch_year + RAMP_YEARS:
            rev = peak * (yr - launch_year + 1) / RAMP_YEARS
        else:
            rev = peak
        if loe_year is not None and yr >= loe_year:
            decay = LOE_DECAY.get(molecule, LOE_DECAY["small_molecule"])
            idx = yr - loe_year
            factor = decay[idx] if idx < len(decay) else decay[-1] * (0.8 ** (idx - len(decay) + 1))
            rev = peak * factor
        if t >= 0:
            curve[t] = rev
    return curve


def compute_asset_rnpv(asset, clinical_rate, base_year=2026, margin=0.35):
    """rNPV 六步。margin=收入转为现金流的经营利润率近似（默认35%）。

    对研发成本与收入统一用同一累积 PoS 概率加权（与玩具 golden 一致，便于闭式校验）。
    """
    phase = normalize_phase(asset.get("current_phase"))
    ta = asset.get("therapeutic_area") or map_indication_to_ta(asset.get("indication"))
    cum_pos = asset.get("cumulative_pos_override") or cumulative_pos(phase, ta)

    peak, pen, comp = estimate_peak_sales(asset, ta)
    launch_year = asset.get("launch_year", base_year + max(1, {"phase1": 8, "phase2": 6,
                            "phase3": 3, "nda": 1, "approved": 0}.get(phase, 5)))
    loe_year = asset.get("loe_year")
    molecule = asset.get("molecule_type", "small_molecule")

    # 若资产直接给定现金流（玩具 golden 场景），走闭式路径
    if asset.get("cashflows"):
        pv = 0.0
        stage_bd = []
        for item in asset["cashflows"]:
            t = item["t"]
            cf = item["cf"]
            wcf = cf * cum_pos
            disc = wcf / ((1 + clinical_rate) ** t)
            pv += disc
            stage_bd.append({"t": t, "cf": cf, "weighted_cf": wcf, "pv": disc})
        return {
            "asset_rnpv": pv, "cumulative_pos": cum_pos, "phase": phase, "ta": ta,
            "peak_sales": peak, "penetration": pen, "competition": comp,
            "stage_breakdown": stage_bd, "launch_year": launch_year,
        }

    curve = build_revenue_curve(peak, launch_year, base_year, loe_year, molecule)
    rd_cost = asset.get("remaining_rd_cost", 0.0)
    rd_years = asset.get("rd_years", list(range(1, max(1, launch_year - base_year) + 1)))
    pv = 0.0
    stage_bd = []
    # 概率加权的剩余研发成本（分摊到 rd_years）
    if rd_years:
        per = rd_cost / len(rd_years)
        for t in rd_years:
            wcf = -per * cum_pos
            disc = wcf / ((1 + clinical_rate) ** t)
            pv += disc
            stage_bd.append({"t": t, "type": "rd_cost", "cf": -per, "weighted_cf": wcf, "pv": disc})
    for t, rev in sorted(curve.items()):
        if t <= 0 or rev == 0:
            continue
        cf = rev * margin
        wcf = cf * cum_pos
        disc = wcf / ((1 + clinical_rate) ** t)
        pv += disc
        stage_bd.append({"t": t, "type": "revenue", "revenue": rev, "cf": cf,
                         "weighted_cf": wcf, "pv": disc})
    return {
        "asset_rnpv": pv, "cumulative_pos": cum_pos, "phase": phase, "ta": ta,
        "peak_sales": peak, "penetration": pen, "competition": comp,
        "stage_breakdown": stage_bd, "launch_year": launch_year,
    }


def decide_paradigm(raw):
    res = raw.get("resolution", {})
    fin = raw.get("financials", {}).get("annual", [{}])
    latest = fin[0] if fin else {}
    rev = latest.get("revenue") or 0
    ni = latest.get("net_income")
    pr = raw.get("pharma_raw", {})
    assets = pr.get("assets", [])
    marketed = [a for a in assets if a.get("marketed")]
    pipeline_late = [a for a in assets if normalize_phase(a.get("current_phase")) in ("phase3", "nda")]
    if rev and rev > 0 and marketed and (pipeline_late or len(assets) > len(marketed)):
        return "large_pharma_sotp"
    if (not rev or rev < (raw.get("_small_rev_threshold", 5e8))) and (ni is None or ni <= 0):
        return "clinical_biotech"
    if marketed and (not pipeline_late):
        return "commercial"
    return "large_pharma_sotp"


def clinical_rate_for(paradigm, assets):
    if paradigm == "clinical_biotech":
        has_late = any(normalize_phase(a.get("current_phase")) in ("phase3", "nda") for a in assets)
        return CLINICAL_DISCOUNT_RATE_BY_PARADIGM["clinical_biotech" if has_late
                                                  else "clinical_biotech_early"]
    return CLINICAL_DISCOUNT_RATE_BY_PARADIGM.get(paradigm, 0.125)


def sensitivity(asset, clinical_rate, base_rnpv, base_year=2026):
    """单因素 tornado：PoS/峰值销售/折现率/上市时间 各 low/base/high。"""
    out = {}
    def rnpv_with(**over):
        a = dict(asset)
        a.update(over)
        return compute_asset_rnpv(a, over.get("_rate", clinical_rate), base_year)["asset_rnpv"]
    # PoS ±30%
    base_pos = compute_asset_rnpv(asset, clinical_rate, base_year)["cumulative_pos"]
    out["pos"] = {
        "low": rnpv_with(cumulative_pos_override=base_pos * 0.7),
        "base": base_rnpv,
        "high": rnpv_with(cumulative_pos_override=min(0.95, base_pos * 1.3)),
    }
    # 峰值销售 ±30%
    _ta = asset.get("therapeutic_area") or map_indication_to_ta(asset.get("indication"))
    peak, _, _ = estimate_peak_sales(asset, _ta)
    if peak:
        out["peak_sales"] = {
            "low": rnpv_with(peak_sales=peak * 0.7, target_patients=None),
            "base": base_rnpv,
            "high": rnpv_with(peak_sales=peak * 1.3, target_patients=None),
        }
    # 折现率 ±200bp
    out["discount_rate"] = {
        "low": compute_asset_rnpv(asset, clinical_rate + 0.02, base_year)["asset_rnpv"],
        "base": base_rnpv,
        "high": compute_asset_rnpv(asset, max(0.01, clinical_rate - 0.02), base_year)["asset_rnpv"],
    }
    # 上市时间 ±2 年
    ly = asset.get("launch_year")
    if ly:
        out["launch_timing"] = {
            "late": rnpv_with(launch_year=ly + 2),
            "base": base_rnpv,
            "early": rnpv_with(launch_year=max(base_year, ly - 2)),
        }
    return out


def build_pipeline(raw):
    pr = raw.get("pharma_raw", {}).get("clinicaltrials", {})
    trials = pr.get("trials", [])
    terminated = [t for t in trials if str(t.get("status", "")).upper() in
                  ("TERMINATED", "WITHDRAWN", "SUSPENDED")]
    return {
        "total_count": pr.get("total_count", len(trials)),
        "source": pr.get("source", "ClinicalTrials.gov v2"),
        "as_of": pr.get("as_of"),
        "trials": trials,
        "terminated": terminated,
    }


def build_loe(raw, as_of):
    pr = raw.get("pharma_raw", {})
    marketed = [a for a in pr.get("assets", []) if a.get("marketed")]
    total_rev = (raw.get("financials", {}).get("annual") or [{}])[0].get("revenue")
    waterfall = []
    for a in marketed:
        rev_share = safe_div(a.get("current_revenue"), total_rev) if a.get("current_revenue") else None
        high_risk = bool(rev_share and rev_share > 0.20 and a.get("loe_year")
                         and a["loe_year"] <= 2026 + 5)
        waterfall.append({
            "asset": a.get("asset"), "loe_year": a.get("loe_year"),
            "revenue_share": rev_share, "molecule_type": a.get("molecule_type"),
            "high_risk_cliff": high_risk,
            "loe_year_source_type": "user_assumption",
        })
    return {"waterfall": waterfall, "note": "专利到期精确日期属付费源，此处为公开信息估计，须人工核对",
            "as_of": as_of}


def build_catalysts(raw, as_of):
    trials = raw.get("pharma_raw", {}).get("clinicaltrials", {}).get("trials", [])
    cats = []
    for t in trials:
        pcd = t.get("primary_completion_date")
        if pcd:
            cats.append({"date": pcd, "event": f"{t.get('intervention','')} {t.get('nct','')} 主要终点预计完成",
                         "type": "primary_completion", "source": "ClinicalTrials.gov"})
    for pdufa in raw.get("pharma_raw", {}).get("pdufa", []):
        cats.append({"date": pdufa.get("date"), "event": pdufa.get("event"), "type": "pdufa",
                     "source": pdufa.get("source", "public")})
    cats.sort(key=lambda c: c.get("date") or "9999")
    return cats


def compute_pharma(raw, wacc_general=0.09):
    res = raw.get("resolution", {})
    pr = raw.get("pharma_raw", {})
    assets_in = pr.get("assets", [])
    as_of = pr.get("clinicaltrials", {}).get("as_of")
    paradigm = decide_paradigm(raw)
    clinical_rate = raw.get("pharma_raw", {}).get("clinical_discount_rate_override") \
        or clinical_rate_for(paradigm, assets_in)

    assets_out = []
    sum_rnpv = 0.0
    checklist = []
    sens_all = {}
    pipeline_assets = [a for a in assets_in if not a.get("marketed")] or assets_in
    for a in pipeline_assets:
        r = compute_asset_rnpv(a, clinical_rate)
        sum_rnpv += r["asset_rnpv"]
        pen_stype = "user_assumption"
        peak_stype = "user_assumption" if not a.get("peak_sales_hard") else "hard"
        assets_out.append({
            "asset": a.get("asset"),
            "indication": a.get("indication"),
            "current_phase": a.get("current_phase"),
            "therapeutic_area": r["ta"],
            "peak_sales": pharma_av(r["peak_sales"], a.get("peak_sales_source", "estimate"),
                                    "患者数×渗透率×年费用×疗程系数", peak_stype, as_of=as_of, unit="百万"),
            "cumulative_pos": pharma_av(r["cumulative_pos"],
                                        "references/pharma-valuation.md PoS基准表",
                                        "从当前阶段到批准各阶段成功率连乘×TA乘子", "benchmark",
                                        as_of=as_of),
            "penetration": pharma_av(r["penetration"], "PENETRATION_BY_COMPETITION",
                                     f"竞争格局({r['competition']})分层默认", pen_stype, as_of=as_of),
            "asset_rnpv": av(r["asset_rnpv"], "pharma_valuation.compute_asset_rnpv",
                             "Σ 概率加权现金流折现 - 概率加权研发成本", as_of=as_of, unit="百万"),
            "stage_breakdown": r["stage_breakdown"],
        })
        # 敏感性
        sens_all[a.get("asset", f"asset{len(sens_all)}")] = sensitivity(a, clinical_rate,
                                                                        r["asset_rnpv"])
        # 核对清单：所有 user_assumption
        checklist.append({"item": f"{a.get('asset')} 渗透率", "value": r["penetration"],
                          "source_type": "user_assumption",
                          "impact_on_rnpv": "↑渗透率 → ↑峰值销售 → ↑rNPV"})
        if peak_stype == "user_assumption":
            checklist.append({"item": f"{a.get('asset')} 峰值销售", "value": r["peak_sales"],
                              "source_type": "user_assumption",
                              "impact_on_rnpv": "↑峰值销售 → ↑rNPV（近似线性）"})
        if a.get("loe_year"):
            checklist.append({"item": f"{a.get('asset')} 专利到期年(LOE)", "value": a.get("loe_year"),
                              "source_type": "user_assumption",
                              "impact_on_rnpv": "LOE越早 → 收入平台越短 → ↓rNPV"})

    def _fin(v):
        """None/NaN → 0，防止 company_rnpv 被污染成 nan。"""
        try:
            if v is None or v != v:  # NaN 自比不等
                return 0.0
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    net_cash = pr.get("net_cash") if pr.get("net_cash") is not None else (raw.get("financials", {}).get("annual") or [{}])[0].get("cash")
    debt = pr.get("debt") if pr.get("debt") is not None else (raw.get("financials", {}).get("annual") or [{}])[0].get("total_debt")
    sum_rnpv = _fin(sum_rnpv)
    company_rnpv = sum_rnpv + _fin(net_cash) - _fin(debt)

    pharma = {
        "paradigm": paradigm,
        "clinical_discount_rate": pharma_av(clinical_rate,
                                            "CLINICAL_DISCOUNT_RATE_BY_PARADIGM",
                                            "独立临床折现率，绝不等于通用WACC", "benchmark"),
        "wacc_general": av(wacc_general, "assumption", "通用DCF折现率(参照，不用于rNPV)", unit="ratio"),
        "double_penalty_check": {
            "clinical_rate_is_independent": abs(clinical_rate - wacc_general) > 1e-9,
            "clinical_rate": clinical_rate,
            "wacc": wacc_general,
            "passed": abs(clinical_rate - wacc_general) > 1e-9,
        },
        "rnpv": {
            "company_rnpv": av(company_rnpv, "pharma_valuation", "Σ资产rNPV + 净现金 - 债务",
                               as_of=as_of, unit="百万"),
            "net_cash": av(net_cash, "financials", "现金及等价物", unit="百万"),
            "debt": av(debt, "financials", "有息负债", unit="百万"),
            "sum_asset_rnpv": av(sum_rnpv, "pharma_valuation", "Σ 各管线资产 rNPV", unit="百万"),
            "assets": assets_out,
        },
        "sensitivity": sens_all,
        "pipeline": build_pipeline(raw),
        "loe": build_loe(raw, as_of),
        "catalysts": build_catalysts(raw, as_of),
        "human_verification_checklist": checklist,
    }
    return pharma


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("analysis")
    ap.add_argument("--wacc", type=float, default=0.09)
    args = ap.parse_args()
    raw = load_json(args.raw)
    analysis = load_json(args.analysis)
    analysis["pharma"] = compute_pharma(raw, wacc_general=args.wacc)
    dump_json(analysis, args.analysis)
    print(args.analysis)


if __name__ == "__main__":
    main()
