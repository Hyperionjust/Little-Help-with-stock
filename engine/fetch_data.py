"""数据层编排：降级链 + 字段级 fallback + 超时重试 + data_gaps 记录。

降级链（references/data-sources.md）：
- A股：tushare(有token) → akshare → efinance → baostock
- 港股：akshare → efinance → yfinance
- 美股：yfinance → akshare
- 医药临床：clinicaltrials + openfda（叠加，非降级）

核心可测逻辑是 merge_fields()：给定有序 provider 结果，逐字段取第一个非 None 并记录来源。
test_providers 用 FakeProvider 直接测这个引擎，不联网。

用法：
  python fetch_data.py resolved.json [-o raw_data.json]
  python fetch_data.py --offline-fixture <fixture.json> [-o raw_data.json]   # 离线回归
"""
from __future__ import annotations
import argparse
import os
import sys
import time

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "."))
import _paths  # noqa: F401,E402  路径引导，见 engine/_paths.py
from _util import load_json, dump_json, now_iso  # noqa: E402


def with_retry(fn, retries=3, base_delay=0.5, timeout_exc=Exception):
    """指数退避重试。返回 (result, error)。失败不抛，交调用方记 data_gaps。"""
    last = None
    for i in range(retries):
        try:
            return fn(), None
        except timeout_exc as e:  # noqa
            last = e
            if i < retries - 1:
                time.sleep(base_delay * (2 ** i))
    return None, last


def merge_fields(field_list, provider_results):
    """字段级 fallback 引擎（纯函数，核心可测逻辑）。

    provider_results: 有序 [(provider_name, {field: value}), ...]，前面的优先。
    返回 (merged{field:value}, field_sources{field:provider}, gaps[field...])。
    对每个字段取第一个非 None 值——**注意是逐字段**，不是整块切源。
    """
    merged, sources, gaps = {}, {}, []
    for f in field_list:
        val, src = None, None
        for pname, res in provider_results:
            if res and res.get(f) is not None:
                val, src = res[f], pname
                break
        merged[f] = val
        if val is not None:
            sources[f] = src
        else:
            gaps.append(f)
    return merged, sources, gaps


# 三表完整性探针：合并后最近一期这些字段齐全即可停止追加 provider
CORE_FINANCIAL_FIELDS = ["revenue", "net_income", "total_assets", "equity", "ocf"]


def merge_financials(provider_results, field_list=None):
    """财报的字段级 fallback（方案阶段1-2，修"整块第一个获胜"）。

    原版行为：第一个返回 annual 的 provider 独占，后面的源即使能补上缺失字段也
    没有机会——一个有 90% 利润表的源会挡住有那缺失 10% 的源。
    现改为：按期间对齐后逐字段取第一个非 None，**逐期记录字段来源**。

    诚实性约定：同一期间混用多源可能混用会计口径（不同源的重述处理不同）。
    因此每期携带 _field_sources，且返回值带 mixed_source_periods 清单——
    填补缺口但绝不掩盖混源事实，下游 statements.py 会把它写进 coverage。
    """
    from base import FINANCIAL_FIELDS
    field_list = field_list or FINANCIAL_FIELDS
    if not provider_results:
        return {"source": "none", "annual": []}

    periods, src_map, order = {}, {}, []
    for pname, res in provider_results:
        for stmt in res.get("annual", []) or []:
            per = stmt.get("period")
            if not per:
                continue
            if per not in periods:
                periods[per] = {"period": per}
                src_map[per] = {}
                order.append(per)
            tgt = periods[per]
            for f in field_list:
                v = stmt.get(f)
                if v is not None and tgt.get(f) is None:
                    tgt[f] = v
                    src_map[per][f] = pname

    annual = [periods[p] for p in sorted(periods, reverse=True)]
    mixed = []
    for stmt in annual:
        fs = src_map[stmt["period"]]
        stmt["_field_sources"] = fs
        if len(set(fs.values())) > 1:
            mixed.append(stmt["period"])

    contributors = list(dict.fromkeys(pn for pn, _ in provider_results))
    return {
        "source": "+".join(contributors),
        "primary_source": contributors[0],
        "accounting_standard": next((r.get("accounting_standard")
                                     for _, r in provider_results
                                     if r.get("accounting_standard")), None),
        "annual": annual,
        "mixed_source_periods": mixed,
    }


def _paid_prefix(market):
    """从付费适配器注册表取该市场可用的 Tier-0 源。缺 registry/pyyaml 时静默返回 []，
    绝不因为付费插槽不可用而影响免费链——免费链是保底。"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "providers", "paid"))
        import registry as _REG
        return _REG.build_paid_prefix(market)
    except Exception:
        return []


def build_chain(market):
    """返回该市场的 provider 降级链（实例列表）。tushare 仅在有 token 时加入。

    腾讯/新浪为轻量 quote 源：既是 fallback，也为跨源价格校验提供独立读数。
    """
    from akshare_p import AksharePorvider
    from efinance_p import EfinanceProvider
    from yfinance_p import YfinanceProvider
    from baostock_p import BaostockProvider
    from tushare_p import TushareProvider
    from tencent_p import TencentProvider
    from sina_p import SinaProvider

    # 顺序原则：快且稳的 quote/kline 源在前（腾讯/新浪/雅虎直连），重接口(akshare 财报)在后但仍会被
    # 财务循环命中。akshare 的 quote 走全市场表且依赖被封的 push2，故排在 quote 早停之后，实际不被调用。
    # 付费终端插槽（方案 §4）：有 key 的付费源升 Tier-0，置于链首。
    # 无任何付费源时 paid_prefix=[]，以下免费链与阶段1完全一致。
    paid_prefix = _paid_prefix(market)

    if market == "A":
        chain = list(paid_prefix)
        if TushareProvider.available():
            chain.append(TushareProvider())
        chain += [TencentProvider(), SinaProvider(), AksharePorvider(),
                  EfinanceProvider(), BaostockProvider()]
        return chain
    if market == "HK":
        return paid_prefix + [TencentProvider(), SinaProvider(), YfinanceProvider(),
                              AksharePorvider(), EfinanceProvider()]
    if market == "US":
        return paid_prefix + [YfinanceProvider(), TencentProvider()]
    return paid_prefix + [AksharePorvider()]


# 基准指数 → 雅虎代码（直连 chart 一条路拿三大基准，最稳）
BENCHMARK_YAHOO = {"沪深300": "000300.SS", "恒生指数": "^HSI", "S&P 500": "^GSPC"}


def fetch_benchmark(resolved):
    """基准指数 K线（相对强弱用）。失败返回空 dict → relative_strength 记 data_gap。"""
    name = resolved.get("benchmark_index")
    ycode = BENCHMARK_YAHOO.get(name)
    if not ycode:
        return {"close": [], "name": name}
    try:
        from yfinance_p import YfinanceProvider
        fake = {"market": "US", "symbol": ycode}
        k = YfinanceProvider().get_kline(fake)
        k["name"] = name
        return k
    except Exception:
        return {"close": [], "name": name}


def fetch_live(resolved):
    """联网抓取（用户环境）。容器内 egress 受限时多数会进 data_gaps——这是预期，不是 bug。"""
    from base import QUOTE_FIELDS
    chain = build_chain(resolved["market"])
    data_gaps = []

    # 逐 provider 取 quote，收集有序结果。跨源价格校验只需 2 个独立读数——集齐即早停，
    # 避免调用慢/被封的重接口(如 akshare 全市场表)拖垮整体耗时。
    quote_results, cross_prices = [], {}
    for p in chain:
        res, err = with_retry(lambda p=p: p.get_quote(resolved), retries=2, base_delay=0.3)
        if err:
            data_gaps.append({"field": f"quote@{p.name}", "reason": str(err)[:120],
                              "providers_tried": [p.name]})
        if res:
            quote_results.append((p.name, res))
            if res.get("price") is not None:
                cross_prices[p.name] = res["price"]
        # 已有 2 个价格读数且市值已拿到 → 早停
        if len(cross_prices) >= 2 and any(r.get("market_cap") for _, r in quote_results):
            break
    q_merged, q_src, q_gaps = merge_fields(QUOTE_FIELDS, quote_results)
    quote = dict(q_merged)
    quote["source"] = q_src.get("price", "unknown")
    quote["as_of"] = now_iso()  # 修缺陷 #18：原版硬编码 None，三件套缺采集时间戳
    quote["cross_source"] = {"prices": cross_prices}
    # 证券名称补全（bare code 输入时 resolution.name 可能为空）
    if not resolved.get("name"):
        for _, r in quote_results:
            if r.get("_name"):
                resolved["name"] = r["_name"]
                break

    # K线（取第一个成功的 provider）
    kline = {}
    for p in chain:
        res, err = with_retry(lambda p=p: p.get_kline(resolved, adjust="qfq"),
                              retries=2, base_delay=0.3)
        if res and res.get("close"):
            kline = res
            break
    if not kline:
        kline = {"adjust": "none", "source": "none", "close": []}
        data_gaps.append({"field": "kline", "reason": "全链K线失败",
                          "providers_tried": [p.name for p in chain]})

    # 财务（字段级合并：主源缺的字段由后备源补，逐期记录来源）
    fin_results = []
    for p in chain:
        res, err = with_retry(lambda p=p: p.get_financials(resolved))
        if res and res.get("annual"):
            fin_results.append((p.name, res))
            probe = merge_financials(fin_results)
            latest = probe["annual"][0] if probe["annual"] else {}
            # 合并后最近一期核心字段齐全且 ≥3 年 → 早停，不再打扰后面的源
            if (len(probe["annual"]) >= 3
                    and all(latest.get(f) is not None for f in CORE_FINANCIAL_FIELDS)):
                break
    financials = merge_financials(fin_results) if fin_results else {"source": "none", "annual": []}
    if not financials.get("annual"):
        data_gaps.append({"field": "financials", "reason": "全链财务失败",
                          "providers_tried": [p.name for p in chain]})

    # 基准指数（相对强弱）
    bench = fetch_benchmark(resolved)
    if not bench.get("close"):
        data_gaps.append({"field": "benchmark_kline", "reason": "基准指数K线获取失败",
                          "providers_tried": ["yahoo.chart_direct"]})

    raw = {
        "resolution": resolved, "quote": quote, "financials": financials,
        "kline": kline, "benchmark_kline": bench,
        "dividend": {"source": "none"}, "estimates": {"source": "none"},
        "data_gaps": data_gaps, "field_sources": q_src,
    }

    # 医药叠加
    if resolved.get("is_pharma"):
        raw["pharma_raw"] = fetch_pharma(resolved, data_gaps, financials)
    return raw


# 阶段排序（选每个药的最高阶段试验）
_PHASE_RANK = {"PHASE4": 6, "PHASE3": 5, "PHASE2/PHASE3": 4.5, "PHASE2": 4,
               "PHASE1/PHASE2": 3.5, "PHASE1": 3, "EARLY_PHASE1": 2, "NA": 0}


def _phase_rank(p):
    return _PHASE_RANK.get(str(p or "").upper().replace(" ", "").replace(",", "/"), 1)


_MARKETED_PHASES = {"PHASE4"}  # Phase4/已批准 → 商业化，不进管线 rNPV


def build_asset_skeletons(trials, top_pipeline=8):
    """从 ClinicalTrials 试验自动生成资产骨架（按药物聚合，取最高阶段）。

    - Phase4/已批准 → marketed=True（商业化组合，供 LOE/SOTP，不算管线 rNPV）。
    - Phase1–3/NDA → 管线资产，取阶段最高的 top_pipeline 个算 rNPV。
    经济参数（患者数/定价）联网拿不到 → 留 None，pharma_valuation 用 TA 基准默认，全部标 user_assumption
    进核对清单。这里只负责把"哪个药、哪个适应症、到哪个阶段"结构化出来。
    """
    by_drug = {}
    for t in trials:
        drug = (t.get("intervention") or "").strip()
        if not drug or str(t.get("study_type", "")).upper() not in ("INTERVENTIONAL", ""):
            continue
        cur = by_drug.get(drug)
        if cur is None or _phase_rank(t.get("phase")) > _phase_rank(cur.get("phase")):
            by_drug[drug] = t

    def mk(t, marketed):
        return {
            "asset": t.get("intervention"), "indication": t.get("indication"),
            "current_phase": t.get("phase"), "marketed": marketed,
            "competition": "moderate", "molecule_type": "small_molecule",
            "target_patients": None, "annual_price_per_patient": None,
            "remaining_rd_cost": 0.0, "_auto_from_clinicaltrials": True,
        }

    marketed, pipeline = [], []
    for t in by_drug.values():
        ph = str(t.get("phase") or "").upper().replace(" ", "")
        if any(m in ph for m in _MARKETED_PHASES):
            marketed.append(mk(t, True))
        else:
            pipeline.append(mk(t, False))
    pipeline.sort(key=lambda a: _phase_rank(a["current_phase"]), reverse=True)
    # 商业化组合保留全部（LOE 用），管线取阶段最高 top_pipeline 个算 rNPV
    return marketed + pipeline[:top_pipeline]


def fetch_pharma(resolved, data_gaps, financials=None):
    """医药叠加：ClinicalTrials(管线) + openFDA(批准) + 自动生成管线资产骨架。"""
    from clinicaltrials_p import ClinicalTrialsProvider
    from openfda_p import OpenFDAProvider
    pr = {"assets": []}
    ct = ClinicalTrialsProvider()
    res, err = with_retry(lambda: ct.get_clinical(resolved))
    if res and res.get("trials"):
        res.setdefault("as_of", None)
        pr["clinicaltrials"] = res
        pr["assets"] = build_asset_skeletons(res["trials"])
    else:
        pr["clinicaltrials"] = {"total_count": 0, "trials": []}
        data_gaps.append({"field": "clinicaltrials", "reason": str(err)[:120] if err else "空",
                          "providers_tried": ["clinicaltrials"]})
    fda = OpenFDAProvider()
    res2, _ = with_retry(lambda: fda.get_approvals(resolved))
    if res2:
        pr["openfda"] = res2
    # 净现金/债务从最新财报兜底
    latest = ((financials or {}).get("annual") or [{}])
    latest = latest[0] if latest else {}
    pr.setdefault("net_cash", latest.get("cash"))
    pr.setdefault("debt", latest.get("total_debt"))
    return pr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("resolved", nargs="?")
    ap.add_argument("--offline-fixture")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    if args.offline_fixture:
        raw = load_json(args.offline_fixture)  # fixture 即 raw_data 形态
    else:
        resolved = load_json(args.resolved)
        raw = fetch_live(resolved)

    out = args.out or f"{raw.get('resolution', {}).get('symbol', 'out')}_raw.json"
    dump_json(raw, out)
    print(out)


if __name__ == "__main__":
    main()
