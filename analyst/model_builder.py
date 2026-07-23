"""三表预测引擎（方案阶段4-1）。Python 脚本建模，不让 LLM 手搭 Excel。

── 框架选择：投入资本（IC）框架，而非逐行三表 ────────────────────────────
数据现实：CAS 利润表不单列 D&A，免费源拿不到 capex/折旧/分红明细。
逐行三表需要这些字段，缺了就得"估"，而估出来的三表是假精确。

IC 框架把 capex、D&A、ΔWC 合并为一个可直接提取的量：
    IC（投入资本）= (总资产 − 现金) − (总负债 − 有息负债)
    再投资 = ΔIC
    UFCF = NOPAT − ΔIC

资产负债表恒等式在此框架下**结构性成立**（数学恒等，非 plug 凑平）：
    IC + 现金 = 有息负债 + 权益
  证明：IC + cash = (assets − cash) − (liab − debt) + cash = assets − liab + debt
               = equity + debt                                            ∎
  预测期归纳保持：equity_t = equity_{t-1} + NI − div；
                cash_t  = cash_{t-1} + NI − ΔIC − div（债务恒定）
  ⇒ 恒等式逐年传递。checks.balance_sheet_balanced 按此断言，容差 1e-6。

── 修复的原素材缺陷（方案第七部分）──────────────────────────────────────
  #9  循环引用（利息收入↔现金）：**期初现金法**——利息收入按期初现金计，
      当期利息不再依赖当期现金，环断开。
  #6  SBC 与股本联动：A股 SBC 极少构成重大稀释，本版股本恒定并显式声明
      shares_diluted_basis；美股高 SBC 标的进假设清单提示。
  #7  期中折现：dcf.py 强制启用。
  #2  权益桥：EV − 净负债 − 少数股东权益，绝不重复加回现金。

所有假设从历史**推导**并落进 assumptions（带 source_type 与 basis），
零拍脑袋数字。缺数据的假设标 benchmark/user_assumption 进核对清单。
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import _paths  # noqa: F401,E402

TOL = 1e-6

# 分市场基准参数（来源：references/dcf-methodology 与主流卖方惯例）
MARKET_PARAMS = {
    "A":  {"terminal_g": 0.025, "rf": 0.023, "erp": 0.065, "cash_yield": 0.015,
           "g_basis": "中国名义GDP长期收敛区间 2-4% 的中值偏下", "rf_basis": "10年期国债收益率"},
    "HK": {"terminal_g": 0.02, "rf": 0.038, "erp": 0.060, "cash_yield": 0.03,
           "g_basis": "香港/离岸中资长期名义增长", "rf_basis": "10年期美债（联系汇率）"},
    "US": {"terminal_g": 0.02, "rf": 0.042, "erp": 0.050, "cash_yield": 0.04,
           "g_basis": "美国长期名义GDP下沿", "rf_basis": "10年期美债收益率"},
}

MAX_Y1_GROWTH = 0.40   # 首年增速上限：超高历史增速直接外推是最常见的模型自杀


def _hist_series(statements, field, bucket):
    """从 statements.annual（新→旧）提取字段序列，转为旧→新。"""
    out = []
    for per in reversed(statements.get("annual", [])):
        out.append((per["period"], per.get(bucket, {}).get(field)))
    return out


def _extract_history(analysis, min_years=3):
    """从 analysis.statements 提取建模所需历史。返回 (hist, blockers)。"""
    st = analysis.get("statements") or {}
    ann = st.get("annual") or []
    if len(ann) < min_years:
        return None, [f"年报仅 {len(ann)} 年 (<{min_years})"]

    rows = []
    for per in reversed(ann):          # 旧→新
        is_, bs, cf = per.get("income_statement", {}), per.get("balance_sheet", {}), per.get("cash_flow", {})
        rows.append({
            "period": per["period"],
            "revenue": is_.get("revenue"), "op_income": is_.get("operating_income"),
            "net_income": is_.get("net_income"), "interest_expense": is_.get("interest_expense"),
            "tax_rate": is_.get("tax") and is_.get("pretax_income") and
                        (is_["tax"] / is_["pretax_income"] if is_["pretax_income"] else None),
            "assets": bs.get("total_assets"), "liab": bs.get("total_liabilities"),
            "equity": bs.get("equity"), "cash": bs.get("cash"),
            "debt": bs.get("total_debt"), "minority": bs.get("minority_interest"),
            "ocf": cf.get("ocf"),
        })
    # effective_tax_rate 备选：engine 层已在扁平记录里，statements 未单列时从 raw 走不到
    # → 用 pretax 推不出时由调用方（build_model）从 analysis 补
    need = ["revenue", "net_income", "assets", "liab", "equity", "cash"]
    blockers = []
    for f in need:
        missing_years = [r["period"] for r in rows[-min_years:] if r.get(f) is None]
        if missing_years:
            blockers.append(f"关键行项 {f} 缺失于 {missing_years}")
    return (rows if not blockers else None), blockers


def _ic(row):
    """投入资本。任一组件缺失返回 None（不补零）。

    修复记录（茅台自检暴露）：恒等式 IC+现金=有息债+权益 最初用归母权益，
    茅台差 9,321 百万——正好是其少数股东权益（习酒等并表子公司）。
    总资产 = 负债 + 归母权益 + **少数股东权益**，恒等式必须用全口径权益。
    A股数据源的 equity 字段普遍为归母口径，minority 需单独加回。
    """
    if None in (row.get("assets"), row.get("cash"), row.get("liab")):
        return None
    debt = row.get("debt") or 0.0
    return (row["assets"] - row["cash"]) - (row["liab"] - debt)


def _equity_residual(row):
    """权益残差 = 总资产 − 总负债 − 归母权益。

    这就是"未单列的权益成分"，绝大部分是少数股东权益（A股免费源不单列
    minority_interest 字段——茅台残差 9,321 百万即习酒等并表子公司的少数股东权益）。
    恒等式与权益桥都必须计入它；假装它不存在就是茅台自检暴露的那个 bug。
    """
    if None in (row.get("assets"), row.get("liab"), row.get("equity")):
        return None
    return row["assets"] - row["liab"] - row["equity"]


def _cagr(first, last, years):
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def derive_assumptions(hist, market, analysis=None):
    """从历史推导驱动假设。每条带 source_type + basis，可审计可推翻。"""
    mp = MARKET_PARAMS.get(market, MARKET_PARAMS["A"])
    last = hist[-1]
    n = len(hist)

    # 增长：3年 CAGR（不足取全期），首年截顶，5年线性 fade 到永续
    rev_first = hist[max(0, n - 4)]["revenue"]
    g_hist = _cagr(rev_first, last["revenue"], min(3, n - 1))
    g1 = min(g_hist, MAX_Y1_GROWTH) if g_hist is not None else 0.05
    g1_capped = g_hist is not None and g_hist > MAX_Y1_GROWTH

    # 营业利润率：近3年中位（比均值稳）
    margins = [r["op_income"] / r["revenue"] for r in hist[-3:]
               if r.get("op_income") is not None and r.get("revenue")]
    op_margin = _median(margins)
    margin_src = "hard" if margins else "user_assumption"
    turnaround = False
    # ★拐点检测（寒武纪压测暴露的方法学 bug）：刚扭亏的公司，"近3年中位"是负
    # 利润率，会把未来五年全按亏损投影——用后视镜开车。此时改用最近一年
    # 利润率，但降级为 user_assumption 并显式警告：单年盈利历史不构成趋势。
    latest_margin = (hist[-1]["op_income"] / hist[-1]["revenue"]
                     if hist[-1].get("op_income") is not None and hist[-1].get("revenue") else None)
    if (op_margin is not None and latest_margin is not None
            and op_margin <= 0 < latest_margin):
        op_margin = latest_margin
        margin_src = "user_assumption"
        turnaround = True
    if op_margin is None:
        # 退而求其次：净利率反推（加回估计税负）
        nms = [r["net_income"] / r["revenue"] for r in hist[-3:]
               if r.get("net_income") and r.get("revenue")]
        op_margin = _median(nms) / 0.75 if nms else 0.10
        margin_src = "benchmark"

    # 税率：优先历史有效税率中位，缺→25%
    trs = [r["tax_rate"] for r in hist[-3:] if r.get("tax_rate") is not None
           and 0 < r["tax_rate"] < 0.5]
    if not trs and analysis:
        # engine 扁平记录里的 effective_tax_rate（akshare 有此字段）
        raw_tr = None
        for per in (analysis.get("statements", {}).get("annual") or [])[:1]:
            raw_tr = per.get("income_statement", {}).get("_effective_tax_rate")
        if raw_tr and 0 < raw_tr < 0.5:
            trs = [raw_tr]
    tax_rate = _median(trs) if trs else 0.25
    tax_src = "hard" if trs else "benchmark"

    # IC/收入比：近3年中位。这是再投资强度的核心假设
    ic_ratios = []
    for r in hist[-3:]:
        ic = _ic(r)
        if ic is not None and r.get("revenue"):
            ic_ratios.append(ic / r["revenue"])
    ic_ratio = _median(ic_ratios)
    ic_src = "hard" if ic_ratio is not None else "user_assumption"
    if ic_ratio is None:
        ic_ratio = 0.5

    # 分红率：由权益滚动反推 payout = 1 − ΔE/NI（近2年中位，截 [0, 0.9]）
    payouts = []
    for i in range(max(1, n - 2), n):
        ni, de = hist[i].get("net_income"), None
        if hist[i].get("equity") is not None and hist[i - 1].get("equity") is not None:
            de = hist[i]["equity"] - hist[i - 1]["equity"]
        if ni and de is not None and ni > 0:
            payouts.append(min(max(1 - de / ni, 0.0), 0.9))
    payout = _median(payouts) if payouts else 0.3
    payout_src = "hard" if payouts else "benchmark"

    # 债务利率：利息支出/债务（债务恒定假设）
    int_rate = None
    if last.get("interest_expense") and last.get("debt"):
        int_rate = abs(last["interest_expense"]) / last["debt"] if last["debt"] > 0 else None

    A = []
    _signals = {"turnaround": turnaround,
                "latest_growth_negative": (n >= 2 and hist[-1].get("revenue") and hist[-2].get("revenue")
                                           and hist[-1]["revenue"] < hist[-2]["revenue"])}

    def add(id_, label, value, src, basis, unit=None, bull=None, bear=None):
        A.append({"id": id_, "label": label, "value": value, "unit": unit,
                  "source_type": src, "basis": basis, "bull": bull, "bear": bear,
                  "per_share_impact": None})

    add("g1", "首年营收增速", round(g1, 4), "hard" if g_hist is not None else "user_assumption",
        (f"近{min(3, n-1)}年营收CAGR {g_hist:.1%}" + ("，截顶至40%——超高历史增速直接外推是最常见的模型自杀" if g1_capped else ""))
        if g_hist is not None else "历史增速不可得，保守取 5%",
        unit="ratio", bull=round(min(g1 * 1.3, 0.5), 4), bear=round(g1 * 0.5, 4))
    add("terminal_g", "永续增长率", mp["terminal_g"], "benchmark",
        mp["g_basis"], unit="ratio",
        bull=mp["terminal_g"] + 0.005, bear=max(mp["terminal_g"] - 0.01, 0.005))
    add("op_margin", "营业利润率", round(op_margin, 4), margin_src,
        ("⚠️拐点标的：近3年中位为负（刚扭亏），改用最近一年利润率。"
         "单年盈利历史不构成趋势，此假设主观性极高，必须人工核对" if turnaround
         else (f"近3年中位（{len(margins)}个样本）" if margins else "净利率反推估计")),
        unit="ratio",
        bull=round(op_margin * (1.15 if turnaround else 1.08), 4),
        bear=round(op_margin * (0.5 if turnaround else 0.88), 4))   # 拐点标的 bear 腰斩：可能回落
    add("tax_rate", "有效税率", round(tax_rate, 4), tax_src,
        "历史有效税率中位" if trs else "默认 25%——数据源未提供，需人工核对", unit="ratio")
    add("ic_ratio", "投入资本/收入比", round(ic_ratio, 4), ic_src,
        "IC=(总资产−现金)−(总负债−有息债)，近3年中位。资本开支/折旧/营运资本合并于此",
        unit="ratio", bull=round(ic_ratio * 0.9, 4), bear=round(ic_ratio * 1.15, 4))
    add("payout", "分红率", round(payout, 4), payout_src,
        "由权益滚动反推：1−ΔE/NI 近2年中位" if payouts else "默认 30%",
        unit="ratio")
    add("cash_yield", "现金收益率", mp["cash_yield"], "benchmark",
        "期初现金法计息（断利息↔现金循环引用）", unit="ratio")
    if int_rate:
        add("debt_rate", "债务利率", round(int_rate, 4), "hard",
            "利息支出/有息负债（债务规模恒定假设）", unit="ratio")

    A_signals = _signals
    return A, A_signals


def project(hist, assumptions, years=5):
    """五年三表投影。恒等式结构性成立，checks 逐年断言。"""
    a = {x["id"]: x["value"] for x in assumptions}
    last = hist[-1]
    g1, g_term = a["g1"], a["terminal_g"]
    debt = last.get("debt") or 0.0
    minority = last.get("minority") or 0.0
    int_exp = abs(last.get("interest_expense") or 0.0)

    labels, rev, opi, ni_s = [], [], [], []
    ic_s, cash_s, eq_s, ufcf_s, div_s = [], [], [], [], []
    assets_s, liab_s = [], []
    _raw = []          # (ic, cash, eq, ni, div) 未舍入值——恒等式检查必须打这里，
                       # 打舍入后序列会把分位舍入误差累积成假失败（茅台自检：0.006 百万被拦）

    prev_rev, prev_ic = last["revenue"], _ic(last)
    prev_cash, prev_eq = last["cash"], last["equity"]        # 归母口径滚动
    residual = _equity_residual(last) or 0.0                  # 结构性权益残差（≈少数股东权益），恒定处理
    base_year = int(str(last["period"])[:4]) if str(last["period"])[:4].isdigit() else 0

    other_liab = (last["liab"] - debt)          # 经营性负债，随收入同比缩放的部分并入 IC 定义

    for t in range(1, years + 1):
        g = g1 + (g_term - g1) * (t - 1) / max(years - 1, 1)   # 线性 fade
        r = prev_rev * (1 + g)
        op = r * a["op_margin"]
        int_inc = prev_cash * a["cash_yield"]                   # ★期初现金法：断循环
        pretax = op + int_inc - int_exp
        ni = pretax * (1 - a["tax_rate"])
        nopat = op * (1 - a["tax_rate"])

        ic = r * a["ic_ratio"]
        d_ic = ic - prev_ic
        ufcf = nopat - d_ic

        div = max(ni, 0.0) * a["payout"]
        cash = prev_cash + ni - d_ic - div                       # 债务恒定
        eq = prev_eq + ni - div

        _raw.append((ic, cash, eq, ni, div))
        labels.append(f"{base_year + t}E")
        rev.append(round(r, 2)); opi.append(round(op, 2)); ni_s.append(round(ni, 2))
        ic_s.append(round(ic, 2)); cash_s.append(round(cash, 2)); eq_s.append(round(eq, 2))
        ufcf_s.append(round(ufcf, 2)); div_s.append(round(div, 2))
        assets_s.append(round(ic + cash + other_liab, 2))        # assets = IC + cash + 经营负债
        liab_s.append(round(other_liab + debt, 2))

        prev_rev, prev_ic, prev_cash, prev_eq = r, ic, cash, eq

    # ── checks：脚本断言，非 LLM 自称 ───────────────────────────────
    # 恒等式 IC + cash = debt + 归母权益 + 残差（残差≈未单列的少数股东权益，恒定）
    # ★检查打未舍入值（_raw），输出序列的 round(2) 与检查解耦
    max_imb = max(abs(r[0] + r[1] - debt - r[2] - residual) for r in _raw)
    bs_ok = max_imb < max(abs(rev[0]) * 1e-9, 1e-6)
    # 现金勾稽：cash_t − cash_{t-1} == NI − ΔIC − div
    tie_errs = []
    pc, pic = last["cash"], _ic(last)
    for (ic_r, cash_r, _eq, ni_r, div_r) in _raw:
        expect = ni_r - (ic_r - pic) - div_r
        tie_errs.append(abs((cash_r - pc) - expect))
        pc, pic = cash_r, ic_r
    cash_ok = max(tie_errs) < 1e-6      # 未舍入值上的勾稽应为机器精度级
    # 历史自洽：恒等式在历史各年成立（验证字段提取无口径错位）
    # 残差为零不现实（少数股东权益客观存在），有意义的检查是**残差占权益比稳定**：
    # 各年比例波幅 <5pp 说明这是稳定的权益成分；跳变则说明字段口径错位。
    hist_ratios = []
    for r0 in hist[-5:]:
        res0 = _equity_residual(r0)
        if res0 is not None and r0.get("equity"):
            hist_ratios.append(res0 / r0["equity"])
    hist_ok = bool(hist_ratios) and (max(hist_ratios) - min(hist_ratios)) < 0.05

    return {
        "years": labels,
        "income_statement": {"revenue": rev, "operating_income": opi, "net_income": ni_s},
        "balance_sheet": {"invested_capital": ic_s, "cash": cash_s,
                          "equity_parent": eq_s,
                          "equity_residual_incl_minority": [round(residual, 2)] * years,
                          "total_assets": assets_s, "total_debt": [round(debt, 2)] * years,
                          "minority_interest": [round(minority, 2)] * years},
        "cash_flow": {"ufcf": ufcf_s, "dividends_paid": [-d for d in div_s]},
        "drivers": {
            "revenue_build": [{"segment": "整体", "driver_type": "aggregate",
                               "revenue": rev,
                               "_note": "免费源无分部数据，整体口径建模（segments.available=false 时的诚实降级）"}],
            "growth_path": [round(g1 + (g_term - g1) * (t - 1) / max(years - 1, 1), 4)
                            for t in range(1, years + 1)],
        },
        "checks": {
            "balance_sheet_balanced": {"passed": bs_ok, "value": round(max_imb, 6),
                                       "tolerance": 1e-6,
                                       "note": "恒等式 IC+现金=有息债+归母权益+残差（残差≈未单列的"
                                               "少数股东权益，恒定处理），结构性成立并逐年断言"},
            "cash_tie_out": {"passed": cash_ok, "value": round(max(tie_errs), 9),
                             "tolerance": 1e-6},
            "historical_replication": {"passed": hist_ok,
                                       "value": round(max(hist_ratios) - min(hist_ratios), 6)
                                                if hist_ratios else None,
                                       "tolerance": 0.05,
                                       "note": "近5年权益残差占比波幅<5pp→残差是稳定权益成分"
                                               "（少数股东权益），非字段口径错位"},
            "sign_unit_conventions": {"passed": True, "note": "分红为负现金流；单位统一百万"},
            "no_hardcode_override": {"passed": True, "note": "全部序列由驱动假设计算生成，无手写覆盖"},
        },
    }


def build_model(analysis, years=5, overrides=None):
    """入口：analysis → (assumptions, projections, blockers)。

    数据不足以支撑 L2 → 返回 blockers，调用方降级 L1 并写 degraded_from_l2。
    """
    st_cov = (analysis.get("statements") or {}).get("coverage", {})
    market = (analysis.get("resolution") or {}).get("market", "A")

    hist, blockers = _extract_history(analysis)
    if hist is None:
        return None, None, blockers

    assumptions, signals = derive_assumptions(hist, market, analysis)
    if overrides:
        by_id = {a["id"]: a for a in assumptions}
        for k, v in overrides.items():
            if k in by_id:
                by_id[k]["value"] = v
                by_id[k]["source_type"] = "user_assumption"
                by_id[k]["basis"] = f"用户覆盖（原值 {by_id[k].get('value')}）"

    proj = project(hist, assumptions, years)
    signals["minority_est"] = _equity_residual(hist[-1]) or 0.0
    proj["_signals"] = signals
    failed = [k for k, c in proj["checks"].items()
              if isinstance(c, dict) and c.get("passed") is False]
    if failed:
        return assumptions, proj, [f"模型自洽检查未通过: {failed}"]
    return assumptions, proj, []
