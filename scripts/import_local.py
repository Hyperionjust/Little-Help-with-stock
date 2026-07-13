"""本地数据导入：把 iFind/同花顺/Wind/Choice/自建库 的导出文件转成 raw_data.json。

为什么：付费数据库（iFind/Wind）的财报字段比免费源更全更准。用户把导出的 xlsx/csv/json 传进来，
本脚本按中文/英文字段别名映射到标准化 raw_data，并把来源标为 Tier-0（优先于免费源）。之后计算/门禁/
渲染四格式流程完全复用——「只加一个入口，不改计算层」。

支持三种导入：
  1) 财务报表宽表（一列一个报告期 或 一行一个报告期）：--financials file.xlsx
  2) 行情/K线（日期,开,高,低,收,量）：--kline file.csv
  3) 已是 raw_data 结构的 json：--raw file.json（原样校验后用）
可与联网互补：本地提供财报，行情仍走免费源（不传 --kline 时联网补）。

用法：
  python import_local.py --symbol 600519 --financials ifind_600519_财报.xlsx [--kline k.csv] -o raw.json
  然后：python run_analysis.py 600519 --offline-fixture raw.json   （或直接把 raw 交给后续脚本）
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _util import dump_json, load_json  # noqa: E402
import resolve_symbol as R  # noqa: E402

# 中文/英文字段别名 → 标准化字段。尽量覆盖 iFind/Wind/同花顺/东财常见命名。
FIELD_ALIASES = {
    "revenue": ["营业总收入", "营业收入", "营收", "total revenue", "revenue", "operating income",
                "营业总收入(元)", "主营业务收入"],
    "cogs": ["营业成本", "营业总成本", "cost of revenue", "cogs", "cost of goods sold"],
    "gross_profit": ["毛利润", "毛利", "gross profit"],
    "operating_income": ["营业利润", "operating profit", "operating income", "经营利润"],
    "net_income": ["归母净利润", "归属于母公司股东的净利润", "归属母公司净利润", "净利润(归母)",
                   "net income", "net profit attributable", "归母净利", "净利润"],
    "ocf": ["经营活动产生的现金流量净额", "经营现金流净额", "经营活动现金流量净额",
            "operating cash flow", "net operating cash flow", "经营性现金流"],
    "total_assets": ["资产总计", "总资产", "total assets"],
    "total_liabilities": ["负债合计", "总负债", "total liabilities"],
    "equity": ["归属于母公司股东权益合计", "归母股东权益", "股东权益(归母)", "归属母公司所有者权益",
               "所有者权益(或股东权益)合计", "total equity", "equity", "shareholders equity",
               "归母净资产"],
    "current_assets": ["流动资产合计", "total current assets", "current assets"],
    "current_liabilities": ["流动负债合计", "total current liabilities", "current liabilities"],
    "inventory": ["存货", "inventory", "inventories"],
    "accounts_receivable": ["应收账款", "应收票据及应收账款", "accounts receivable", "receivables"],
    "cash": ["货币资金", "现金及现金等价物", "cash", "cash and cash equivalents"],
    "total_debt": ["有息负债", "带息债务", "总有息负债", "total debt", "interest bearing debt"],
    "interest_expense": ["利息费用", "利息支出", "interest expense"],
    "effective_tax_rate": ["实际税率", "有效税率", "effective tax rate"],
    "eps_diluted": ["稀释每股收益", "每股收益(稀释)", "diluted eps", "基本每股收益", "每股收益"],
    "depreciation": ["折旧与摊销", "折旧摊销", "depreciation and amortization", "depreciation"],
}
# 行情列别名
KLINE_ALIASES = {
    "dates": ["日期", "时间", "date", "trade_date", "交易日期"],
    "open": ["开盘", "开盘价", "open"],
    "high": ["最高", "最高价", "high"],
    "low": ["最低", "最低价", "low"],
    "close": ["收盘", "收盘价", "close", "adj close", "后复权价", "前复权价"],
    "volume": ["成交量", "volume", "vol"],
}
QUOTE_ALIASES = {
    "price": ["最新价", "现价", "收盘价", "price", "last"],
    "market_cap": ["总市值", "市值", "market cap", "total mv"],
    "prev_close": ["昨收", "前收盘", "prev close", "previous close"],
    "float_shares": ["流通股", "流通股本", "float shares"],
    "total_shares": ["总股本", "total shares"],
}


def _norm(s):
    return str(s).strip().lower().replace(" ", "").replace("_", "").replace("(元)", "").replace("（元）", "")


def _build_alias_lookup(alias_map):
    lut = {}
    for std, names in alias_map.items():
        for n in names:
            lut[_norm(n)] = std
    return lut


def _to_million(v, unit_hint):
    """按单位提示把金额转百万。iFind/Wind 常用元/万元/亿元。"""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    mult = {"元": 1e-6, "万元": 1e-2, "万": 1e-2, "亿元": 100.0, "亿": 100.0,
            "百万": 1.0, "百万元": 1.0, "million": 1.0}.get(unit_hint, 1e-6)
    return v * mult


def _read_table(path):
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        return pd.read_excel(path, header=None)
    return pd.read_csv(path, header=None, encoding_errors="ignore")


def import_financials(path, unit_hint="元"):
    """自动识别宽表方向（报告期在列 or 在行），返回 annual 列表（最近财年 index 0）。"""
    import pandas as pd
    df = _read_table(path)
    lut = _build_alias_lookup(FIELD_ALIASES)

    def score_orientation(frame):
        """统计首列/首行里能匹配到标准字段的数量，判断指标是在行标签还是列标签。"""
        col0 = sum(1 for x in frame.iloc[:, 0] if _norm(x) in lut)
        row0 = sum(1 for x in frame.iloc[0, :] if _norm(x) in lut)
        return col0, row0

    col0, row0 = score_orientation(df)
    # 指标在首列（每行一个指标，每列一个报告期）——iFind 常见
    if col0 >= row0:
        header = [str(x) for x in df.iloc[0, :]]  # 首行是报告期
        periods = header[1:]
        data = {}
        for _, r in df.iloc[1:].iterrows():
            std = lut.get(_norm(r.iloc[0]))
            if not std:
                continue
            for j, per in enumerate(periods, start=1):
                data.setdefault(per, {})[std] = r.iloc[j]
        recs = [(per, vals) for per, vals in data.items()]
    else:
        # 指标在首行（每列一个指标，每行一个报告期）
        header = [_norm(x) for x in df.iloc[0, :]]
        std_cols = {j: lut[h] for j, h in enumerate(header) if h in lut}
        period_col = 0
        recs = []
        for _, r in df.iloc[1:].iterrows():
            per = str(r.iloc[period_col])
            vals = {std_cols[j]: r.iloc[j] for j in std_cols}
            recs.append((per, vals))

    annual = []
    for per, vals in recs:
        row = {"period": str(per), "report_date": _period_to_date(str(per))}
        money_fields = set(FIELD_ALIASES) - {"effective_tax_rate", "eps_diluted"}
        for std, v in vals.items():
            if std in money_fields:
                row[std] = _to_million(v, unit_hint)
            else:
                try:
                    row[std] = float(v) if str(v).strip() not in ("", "nan") else None
                except (TypeError, ValueError):
                    row[std] = None
        if row.get("gross_profit") is None and row.get("revenue") and row.get("cogs"):
            row["gross_profit"] = row["revenue"] - row["cogs"]
        annual.append(row)
    # 最近财年在前
    annual.sort(key=lambda x: x.get("report_date") or x.get("period"), reverse=True)
    return annual


def _period_to_date(per):
    import re
    m = re.search(r"(20\d{2})", per)
    if not m:
        return None
    y = m.group(1)
    if "一季" in per or "Q1" in per.upper() or "0331" in per:
        return f"{y}-03-31"
    if "中" in per or "半年" in per or "Q2" in per.upper() or "0630" in per:
        return f"{y}-06-30"
    if "三季" in per or "Q3" in per.upper() or "0930" in per:
        return f"{y}-09-30"
    return f"{y}-12-31"


def import_kline(path):
    import pandas as pd
    df = _read_table(path)
    lut = _build_alias_lookup(KLINE_ALIASES)
    header = [_norm(x) for x in df.iloc[0, :]]
    colmap = {j: lut[h] for j, h in enumerate(header) if h in lut}
    if "close" not in colmap.values():
        return {}
    out = {"adjust": "qfq", "source": "local_import", "as_of": None,
           "dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
    for _, r in df.iloc[1:].iterrows():
        for j, std in colmap.items():
            val = r.iloc[j]
            if std == "dates":
                out["dates"].append(str(val))
            else:
                try:
                    out[std].append(float(val))
                except (TypeError, ValueError):
                    out[std].append(None)
    if out["dates"]:
        out["as_of"] = out["dates"][-1]
    return out


def import_quote_row(path):
    """可选：单行行情表 → quote。"""
    import pandas as pd
    df = _read_table(path)
    lut = _build_alias_lookup(QUOTE_ALIASES)
    header = [_norm(x) for x in df.iloc[0, :]]
    colmap = {j: lut[h] for j, h in enumerate(header) if h in lut}
    if not colmap:
        return {}
    r = df.iloc[1]
    out = {}
    for j, std in colmap.items():
        try:
            out[std] = float(r.iloc[j])
        except (TypeError, ValueError):
            out[std] = None
    return out


def _complement_live(raw, resolved):
    """用免费源联网补齐本地导入缺的部分（quote/kline/benchmark/临床），但保留本地财报为准。

    典型用法：iFind 导出财报（更全更准）+ 现价/K线走免费源 → 完整不降级报告。
    """
    import fetch_data as FD
    try:
        live = FD.fetch_live(resolved)
    except Exception as e:
        raw["data_gaps"].append({"field": "live_complement", "reason": str(e)[:120],
                                 "providers_tried": ["fetch_live"]})
        return raw
    if not raw.get("quote", {}).get("price"):
        raw["quote"] = live.get("quote", raw["quote"])
    if not raw.get("kline", {}).get("close"):
        raw["kline"] = live.get("kline", raw["kline"])
    if not raw.get("benchmark_kline", {}).get("close"):
        raw["benchmark_kline"] = live.get("benchmark_kline", {"close": []})
    # 医药：本地无临床 → 用联网 clinicaltrials 补
    if resolved.get("is_pharma") and live.get("pharma_raw"):
        lp = live["pharma_raw"]
        raw.setdefault("pharma_raw", {})
        if not raw["pharma_raw"].get("assets"):
            raw["pharma_raw"] = lp
        # 净现金/债务优先用本地财报
        latest = (raw.get("financials", {}).get("annual") or [{}])[0]
        raw["pharma_raw"].setdefault("net_cash", latest.get("cash"))
        raw["pharma_raw"].setdefault("debt", latest.get("total_debt"))
    raw["_import_tier"] = 0  # 本地财报仍是 Tier-0
    raw["financials"]["source"] += " + live_quote_kline"
    return raw


def build_raw(symbol, financials_path=None, kline_path=None, quote_path=None,
              unit_hint="元", industry=None, name=None, complement_live=False):
    resolved = R.resolve(symbol, industry_hint=industry, name_hint=name)
    raw = {"resolution": resolved, "quote": {"source": "local_import", "as_of": None,
           "cross_source": {"prices": {}}},
           "financials": {"source": "none", "annual": []},
           "kline": {"adjust": "none", "source": "none", "close": []},
           "benchmark_kline": {"close": []}, "dividend": {"source": "none"},
           "estimates": {"source": "none"}, "data_gaps": [],
           "field_sources": {}, "_import_tier": 0}
    if financials_path:
        annual = import_financials(financials_path, unit_hint)
        raw["financials"] = {"source": f"local_import:{os.path.basename(financials_path)}",
                             "accounting_standard": None, "annual": annual}
    if kline_path:
        raw["kline"] = import_kline(kline_path)
    if quote_path:
        q = import_quote_row(quote_path)
        raw["quote"].update(q)
        if q.get("price"):
            raw["quote"]["cross_source"]["prices"] = {"local_import": q["price"]}
    # 医药：本地导入不含临床数据 → 提示后续联网补 clinicaltrials
    if resolved.get("is_pharma"):
        raw["pharma_raw"] = {"assets": [], "clinicaltrials": {"total_count": None, "trials": []},
                             "_note": "本地导入不含临床管线，建议联网跑 clinicaltrials 补全或手工提供 assets"}
        raw["data_gaps"].append({"field": "clinicaltrials",
                                 "reason": "本地导入模式：临床管线需联网或手工补",
                                 "providers_tried": ["local_import"]})
    if complement_live:
        raw = _complement_live(raw, resolved)
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--financials")
    ap.add_argument("--kline")
    ap.add_argument("--quote")
    ap.add_argument("--raw", help="已是 raw_data 结构的 json，原样校验后用")
    ap.add_argument("--unit", default="元", help="金额单位：元/万元/亿元/百万/million")
    ap.add_argument("--industry")
    ap.add_argument("--name")
    ap.add_argument("--complement-live", action="store_true",
                    help="用免费源联网补齐现价/K线/临床（本地财报仍为准）→ 完整不降级报告")
    ap.add_argument("-o", "--out", default="raw_imported.json")
    args = ap.parse_args()

    if args.raw:
        raw = load_json(args.raw)
    else:
        raw = build_raw(args.symbol, args.financials, args.kline, args.quote,
                        unit_hint=args.unit, industry=args.industry, name=args.name,
                        complement_live=args.complement_live)
    dump_json(raw, args.out)
    n = len(raw.get("financials", {}).get("annual", []))
    print(f"{args.out}  (financials periods={n}, kline points={len(raw.get('kline',{}).get('close',[]))})")


if __name__ == "__main__":
    main()
