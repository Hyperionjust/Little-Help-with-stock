"""Fundamental ratio library. Pure functions over raw_data['financials'].

输入：raw_data（fetch_data 产出）。输出：valuation / profitability / reverse_validation /
solvency / growth 五个块，每个数字都是 av() 三件套。所有算术在此，SKILL.md 不出现公式。

财报约定：raw_data['financials']['annual'] 是年度报表列表，**最近财年在 index 0**。
可选 'ttm' 字段；缺失时用最近年报作 TTM 近似并在 period 标注。
"""
from __future__ import annotations
from _util import av, safe_div, pct, avg2


def _annual(raw):
    return raw.get("financials", {}).get("annual", [])


def _ttm(raw):
    """Return the statement to use as TTM. Prefer explicit ttm, else latest annual (labelled)."""
    fin = raw.get("financials", {})
    if fin.get("ttm"):
        t = dict(fin["ttm"])
        t.setdefault("_ttm_label", t.get("period", "TTM"))
        t["_is_proxy"] = False
        return t
    ann = _annual(raw)
    if not ann:
        return None
    t = dict(ann[0])
    t["_ttm_label"] = f"{t.get('period','latest')} (annual proxy)"
    t["_is_proxy"] = True
    return t


def _src(raw, field):
    return raw.get("field_sources", {}).get(field, raw.get("financials", {}).get("source", "unknown"))


def _asof(raw, stmt):
    return stmt.get("report_date") if stmt else raw.get("quote", {}).get("as_of")


def compute_valuation(raw):
    q = raw.get("quote", {})
    price = (q.get("price") or {}).get("value") if isinstance(q.get("price"), dict) else q.get("price")
    mcap = (q.get("market_cap") or {}).get("value") if isinstance(q.get("market_cap"), dict) else q.get("market_cap")
    ttm = _ttm(raw)
    ann = _annual(raw)
    lyr = ann[0] if ann else None
    src_fin = raw.get("financials", {}).get("source", "unknown")
    out = {"default_pe_basis": "ttm"}

    ni_ttm = ttm.get("net_income") if ttm else None
    rev_ttm = ttm.get("revenue") if ttm else None
    period = ttm.get("_ttm_label") if ttm else None

    out["pe_ttm"] = av(safe_div(mcap, ni_ttm), src_fin,
                       "market_cap / 归母净利_TTM", as_of=_asof(raw, ttm),
                       unit="x", period=period)
    # 静态 PE 用上一完整财年
    ni_lyr = lyr.get("net_income") if lyr else None
    out["pe_static"] = av(safe_div(mcap, ni_lyr), src_fin,
                          "market_cap / 归母净利_上一财年", as_of=_asof(raw, lyr),
                          unit="x", period=(lyr.get("period") if lyr else None))
    # 预测 PE
    est = raw.get("estimates", {}).get("net_income_fy")
    out["pe_forward"] = av(safe_div(mcap, est), raw.get("estimates", {}).get("source", "estimates"),
                           "market_cap / 归母净利_一致预期", unit="x")

    equity = ttm.get("equity") if ttm else None
    out["pb"] = av(safe_div(mcap, equity), src_fin, "market_cap / 归母净资产(最新期)",
                   as_of=_asof(raw, ttm), unit="x")
    out["ps_ttm"] = av(safe_div(mcap, rev_ttm), src_fin, "market_cap / 营收_TTM",
                       as_of=_asof(raw, ttm), unit="x", period=period)

    # PEG 用净利 3 年 CAGR（百分数值）
    g = compute_growth(raw)
    ni_cagr3 = (g.get("net_income_cagr_3y") or {}).get("value")
    peg_val = None
    if out["pe_ttm"]["value"] is not None and ni_cagr3 is not None and ni_cagr3 > 0:
        peg_val = out["pe_ttm"]["value"] / ni_cagr3
    out["peg"] = av(peg_val, src_fin, "PE_TTM / (净利3年CAGR×100)；CAGR<=0则null", unit="x")

    # EV/EBITDA
    if ttm:
        ebitda = ttm.get("ebitda")
        if ebitda is None and ttm.get("operating_income") is not None:
            dep = ttm.get("depreciation") or 0
            ebitda = ttm["operating_income"] + dep
        total_debt = ttm.get("total_debt")
        cash = ttm.get("cash")
        ev = None
        if mcap is not None and total_debt is not None and cash is not None:
            ev = mcap + total_debt - cash
        out["ev_ebitda"] = av(safe_div(ev, ebitda), src_fin,
                              "(市值+有息负债-现金)/EBITDA_TTM；EBITDA=营业利润+折旧摊销", unit="x")
    else:
        out["ev_ebitda"] = av(None, src_fin, "EV/EBITDA", unit="x")

    div = raw.get("dividend", {})
    dy = None
    if div.get("dps") is not None and price:
        dy = pct(safe_div(div["dps"], price))
    elif div.get("total") is not None and mcap:
        dy = pct(safe_div(div["total"], mcap))
    out["dividend_yield"] = av(dy, div.get("source", src_fin), "每股股息/现价 或 分红总额/市值", unit="%")
    return out


def compute_profitability(raw):
    ttm = _ttm(raw)
    ann = _annual(raw)
    src = raw.get("financials", {}).get("source", "unknown")
    out = {}
    if not ttm:
        return {"roe": av(None, src, "缺财报")}
    ni = ttm.get("net_income")
    rev = ttm.get("revenue")
    prev = ann[1] if len(ann) > 1 else None

    equity_avg = avg2(ttm.get("equity"), (prev or {}).get("equity")) if prev else ttm.get("equity")
    assets_avg = avg2(ttm.get("total_assets"), (prev or {}).get("total_assets")) if prev else ttm.get("total_assets")

    out["roe"] = av(pct(safe_div(ni, equity_avg)), src, "归母净利_TTM / 归母净资产均值", unit="%",
                    period=ttm.get("_ttm_label"))
    out["roa"] = av(pct(safe_div(ni, assets_avg)), src, "归母净利_TTM / 总资产均值", unit="%")

    # ROIC
    op = ttm.get("operating_income")
    tax_rate = ttm.get("effective_tax_rate")
    tax_is_default = tax_rate is None
    if tax_is_default:
        tax_rate = 0.25  # 缺失时的默认假设——必须在公式里披露（缺陷 #29：原版静默使用）
    nopat = op * (1 - tax_rate) if op is not None else None
    invested = None
    if ttm.get("total_debt") is not None and ttm.get("equity") is not None:
        invested = ttm["total_debt"] + ttm["equity"]
    _roic_formula = ("NOPAT/(有息负债+权益)；NOPAT=营业利润×(1-有效税率"
                     + ("，默认0.25——数据源未提供实际税率，需人工核对" if tax_is_default else "")
                     + ")")
    out["roic"] = av(pct(safe_div(nopat, invested)), src, _roic_formula, unit="%",
                     **({"source_type": "benchmark"} if tax_is_default else {}))

    gp = ttm.get("gross_profit")
    if gp is None and rev is not None and ttm.get("cogs") is not None:
        gp = rev - ttm["cogs"]
    out["gross_margin"] = av(pct(safe_div(gp, rev)), src, "毛利/营收_TTM", unit="%")
    out["net_margin"] = av(pct(safe_div(ni, rev)), src, "归母净利/营收_TTM", unit="%")
    out["cash_content"] = av(safe_div(ttm.get("ocf"), ni), src, "经营现金流_TTM / 归母净利_TTM", unit="x")

    # 杜邦
    nm = safe_div(ni, rev)
    at = safe_div(rev, assets_avg)
    em = safe_div(assets_avg, equity_avg)
    prod = None
    if None not in (nm, at, em):
        prod = nm * at * em
    out["dupont"] = {
        "net_margin": av(pct(nm), src, "归母净利/营收", unit="%"),
        "asset_turnover": av(at, src, "营收/总资产均值", unit="x"),
        "equity_multiplier": av(em, src, "总资产均值/权益均值", unit="x"),
        "product_check": av(pct(prod), src, "净利率×资产周转×权益乘数 应≈ROE", unit="%"),
    }
    return out


def _yoy(cur, prev):
    return safe_div(cur - prev, prev) if (cur is not None and prev is not None) else None


def compute_growth(raw):
    ann = _annual(raw)
    src = raw.get("financials", {}).get("source", "unknown")
    out = {}
    if len(ann) < 2:
        return {"revenue_yoy": av(None, src, "缺足够年报")}
    r0, r1 = ann[0].get("revenue"), ann[1].get("revenue")
    n0, n1 = ann[0].get("net_income"), ann[1].get("net_income")
    out["revenue_yoy"] = av(pct(_yoy(r0, r1)), src, "营收_t / 营收_{t-1} - 1", unit="%")
    out["net_income_yoy"] = av(pct(_yoy(n0, n1)), src, "净利_t / 净利_{t-1} - 1", unit="%")

    # QoQ 需季度数据
    q = raw.get("financials", {}).get("quarterly", [])
    qoq = None
    if len(q) >= 2 and q[0].get("revenue") and q[1].get("revenue"):
        qoq = pct(_yoy(q[0]["revenue"], q[1]["revenue"]))
    out["revenue_qoq"] = av(qoq, src, "营收_q / 营收_{q-1} - 1", unit="%")

    def cagr(field, years):
        if len(ann) <= years:
            return None
        a = ann[0].get(field)
        b = ann[years].get(field)
        if a is None or b is None or b <= 0 or a <= 0:
            return None
        return (a / b) ** (1.0 / years) - 1.0

    out["revenue_cagr_3y"] = av(pct(cagr("revenue", 3)), src, "(营收_t/营收_{t-3})^(1/3)-1", unit="%")
    out["revenue_cagr_5y"] = av(pct(cagr("revenue", 5)), src, "(营收_t/营收_{t-5})^(1/5)-1", unit="%")
    out["net_income_cagr_3y"] = av(pct(cagr("net_income", 3)), src, "(净利_t/净利_{t-3})^(1/3)-1", unit="%")
    return out


def compute_reverse_validation(raw):
    ann = _annual(raw)
    src = raw.get("financials", {}).get("source", "unknown")
    out = {"flags": []}
    if len(ann) < 2:
        out["ar_vs_revenue_growth"] = av(None, src, "缺足够年报")
        return out
    c, p = ann[0], ann[1]
    rev_g = _yoy(c.get("revenue"), p.get("revenue"))
    ar_g = _yoy(c.get("accounts_receivable"), p.get("accounts_receivable"))
    inv_g = _yoy(c.get("inventory"), p.get("inventory"))

    ar_gap = None if (ar_g is None or rev_g is None) else pct(ar_g - rev_g)
    inv_gap = None if (inv_g is None or rev_g is None) else pct(inv_g - rev_g)
    out["ar_vs_revenue_growth"] = av(ar_gap, src, "应收增速 - 营收增速(YoY)，正值=回款质量存疑", unit="pp")
    out["inventory_vs_revenue_growth"] = av(inv_gap, src, "存货增速 - 营收增速(YoY)，正值=积压风险", unit="pp")

    # OCF-NI 3年背离度
    div_terms = []
    for st in ann[:3]:
        ocf, ni = st.get("ocf"), st.get("net_income")
        if ocf is not None and ni is not None and abs(ni) > 1e-9:
            div_terms.append((ocf - ni) / abs(ni))
    div3 = sum(div_terms) / len(div_terms) if div_terms else None
    out["ocf_ni_divergence_3y"] = av(div3, src, "mean((OCF_t-NI_t)/|NI_t|) 近3年，持续负=利润未转现金", unit="ratio")

    # flags 红黄绿灯
    def flag(name, val, thresh, worse_high=True):
        if val is None:
            return
        bad = (val > thresh) if worse_high else (val < thresh)
        level = "red" if bad else ("green" if (val < 0) == worse_high else "yellow")
        out["flags"].append({"name": name, "value": val, "level": level})

    flag("应收增速超营收", ar_gap, 15)
    flag("存货增速超营收", inv_gap, 15)
    if div3 is not None:
        out["flags"].append({"name": "OCF/NI背离", "value": div3,
                             "level": "red" if div3 < -0.2 else "green"})
    return out


def compute_solvency(raw):
    ttm = _ttm(raw)
    ann = _annual(raw)
    src = raw.get("financials", {}).get("source", "unknown")
    out = {}
    if not ttm:
        return {"debt_to_asset": av(None, src, "缺财报")}
    ta = ttm.get("total_assets")
    tl = ttm.get("total_liabilities")
    ca = ttm.get("current_assets")
    cl = ttm.get("current_liabilities")
    inv = ttm.get("inventory")
    out["debt_to_asset"] = av(pct(safe_div(tl, ta)), src, "总负债/总资产", unit="%")
    out["current_ratio"] = av(safe_div(ca, cl), src, "流动资产/流动负债", unit="x")
    qa = (ca - inv) if (ca is not None and inv is not None) else None
    out["quick_ratio"] = av(safe_div(qa, cl), src, "(流动资产-存货)/流动负债", unit="x")
    ebit = ttm.get("operating_income")
    ie = ttm.get("interest_expense")
    out["interest_coverage"] = av(safe_div(ebit, ie) if ie else None, src,
                                  "EBIT/利息费用；利息≈0则null", unit="x")
    rev = ttm.get("revenue")
    cogs = ttm.get("cogs")
    prev = ann[1] if len(ann) > 1 else None
    ar_avg = avg2(ttm.get("accounts_receivable"), (prev or {}).get("accounts_receivable"))
    inv_avg = avg2(inv, (prev or {}).get("inventory"))
    out["ar_days"] = av(safe_div(365 * ar_avg, rev) if ar_avg is not None else None, src,
                        "365×应收均值/营收_TTM", unit="天")
    out["inventory_days"] = av(safe_div(365 * inv_avg, cogs) if inv_avg is not None else None, src,
                               "365×存货均值/销货成本_TTM", unit="天")
    return out
