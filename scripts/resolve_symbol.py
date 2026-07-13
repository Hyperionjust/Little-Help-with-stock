"""输入解析：把用户各种写法标准化为 {market, symbol, name, currency, exchange, industry_tag, is_pharma}.

支持：600519 / sh600519 / 600519.SH / 000001.SZ / 中文名；00700 / hk00700 / 0700.HK / 名称；
AAPL / 105.AAPL / Apple。行业标签判定命中医药 → is_pharma=True 激活医药模块。

行业判定第一版基于内置名录 + 关键词（离线可用）；联网环境可由 provider 补全行业字段后覆盖。
用法：python resolve_symbol.py "<输入>"  → 打印 JSON
"""
from __future__ import annotations
import argparse
import json
import re
import sys

# 已知名称/代码 → 市场映射（可扩展；联网时用 provider 的证券主表覆盖）
NAME_TO_SYMBOL = {
    "贵州茅台": ("A", "600519"), "茅台": ("A", "600519"),
    "腾讯控股": ("HK", "00700"), "腾讯": ("HK", "00700"),
    "apple": ("US", "AAPL"), "苹果": ("US", "AAPL"),
    "恒瑞医药": ("A", "600276"), "恒瑞": ("A", "600276"),
    "百济神州": ("US", "BGNE"), "beigene": ("US", "BGNE"),
    "药明康德": ("A", "603259"), "迈瑞医疗": ("A", "300760"),
}
# 医药标签判定：内置医药代码 + 名称关键词 + 行业字符串关键词
PHARMA_SYMBOLS = {"600276", "603259", "300760", "BGNE", "688180", "01801", "02359", "06160"}
PHARMA_NAME_KW = ["医药", "生物", "制药", "疫苗", "药业", "医疗", "pharma", "bio", "therapeutics",
                  "biotech", "medicine", "health"]
PHARMA_INDUSTRY_KW = ["医药生物", "health care", "healthcare", "医疗保健", "biotech", "pharmaceutical"]

MARKET_META = {
    "A": {"currency": "CNY", "benchmark_index": "沪深300"},
    "HK": {"currency": "HKD", "benchmark_index": "恒生指数"},
    "US": {"currency": "USD", "benchmark_index": "S&P 500"},
}

# ClinicalTrials.gov 用英文 sponsor 名检索管线；中文名标的需映射到其试验注册主办方英文名。
# 未覆盖的公司管线可能拉空（会进 data_gaps/warning），用户可在 CLI 传 --sponsor 覆盖。
CLINICAL_SPONSOR_ALIASES = {
    "600276": "Hengrui", "恒瑞医药": "Hengrui", "恒瑞": "Hengrui",
    "BGNE": "BeiGene", "百济神州": "BeiGene", "ONC": "BeiGene",
    "603259": "WuXi AppTec", "药明康德": "WuXi AppTec",
    "300760": "Mindray", "迈瑞医疗": "Mindray",
    "688180": "Junshi", "君实生物": "Junshi",
    "01801": "Innovent", "信达生物": "Innovent",
    "02359": "WuXi Biologics", "药明生物": "WuXi Biologics",
    "06160": "BeiGene",  # 百济港股
    "01177": "Sino Biopharmaceutical", "中国生物制药": "Sino Biopharmaceutical",
    "02196": "Fosun Pharma", "复星医药": "Fosun Pharma",
}


def _exchange_for_a(sym):
    if sym.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "SSE", ("STAR" if sym.startswith("688") else "SSE")
    if sym.startswith(("000", "001", "002", "003", "300", "301", "200")):
        return "SZSE", ("ChiNext" if sym.startswith(("300", "301")) else "SZSE")
    return "SSE", "SSE"


def resolve(raw_input, industry_hint=None, name_hint=None):
    s = raw_input.strip()
    low = s.lower()
    market = symbol = name = None

    # 中文/英文名直接命中
    if low in NAME_TO_SYMBOL:
        market, symbol = NAME_TO_SYMBOL[low]
        name = s
    else:
        for nm, (mk, sym) in NAME_TO_SYMBOL.items():
            if nm in low or nm in s:
                market, symbol, name = mk, sym, nm
                break

    if symbol is None:
        # 带后缀 .SH/.SZ/.HK
        m = re.match(r"^(\d{4,6})\.(sh|sz|hk)$", low)
        if m:
            code, suf = m.group(1), m.group(2)
            if suf == "hk":
                market, symbol = "HK", code.zfill(5)
            else:
                market, symbol = "A", code
        # 前缀 sh/sz/hk
        if symbol is None:
            m = re.match(r"^(sh|sz|hk)(\d{4,6})$", low)
            if m:
                pre, code = m.group(1), m.group(2)
                if pre == "hk":
                    market, symbol = "HK", code.zfill(5)
                else:
                    market, symbol = "A", code
        # 美股 105.AAPL 形式
        if symbol is None:
            m = re.match(r"^\d+\.([a-z]{1,5})$", low)
            if m:
                market, symbol = "US", m.group(1).upper()
        # 纯数字
        if symbol is None and re.match(r"^\d{4,6}$", s):
            if len(s) == 5 or s.startswith("0"):
                # 5 位或 0 开头判港股（A股无 5 位；A股 000/001 等已在上面名称命中前留给此处）
                if len(s) == 5:
                    market, symbol = "HK", s
                elif len(s) == 6:
                    market, symbol = "A", s
                else:
                    market, symbol = "HK", s.zfill(5)
            elif len(s) == 6:
                market, symbol = "A", s
            else:
                market, symbol = "HK", s.zfill(5)
        # 纯字母美股
        if symbol is None and re.match(r"^[a-zA-Z]{1,5}$", s):
            market, symbol = "US", s.upper()

    if symbol is None:
        raise ValueError(f"无法解析输入: {raw_input!r}")

    meta = MARKET_META[market]
    exchange = None
    if market == "A":
        _, exchange = _exchange_for_a(symbol)
    elif market == "HK":
        exchange = "HKEX"
    else:
        exchange = "US"

    # 医药判定
    industry_tag = industry_hint
    hay = f"{symbol} {name or ''} {s} {industry_hint or ''} {name_hint or ''}".lower()
    is_pharma = (symbol in PHARMA_SYMBOLS or
                 any(k in hay for k in PHARMA_NAME_KW) or
                 any(k in (industry_hint or "").lower() for k in PHARMA_INDUSTRY_KW))

    sponsor = (CLINICAL_SPONSOR_ALIASES.get(symbol)
               or CLINICAL_SPONSOR_ALIASES.get(name or "")
               or (name if (name and name.isascii()) else None))
    return {
        "market": market, "symbol": symbol, "name": name or name_hint,
        "currency": meta["currency"], "exchange": exchange,
        "industry_tag": industry_tag, "is_pharma": bool(is_pharma),
        "benchmark_index": meta["benchmark_index"], "raw_input": raw_input,
        "clinical_sponsor": sponsor,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--industry")
    ap.add_argument("--name")
    args = ap.parse_args()
    try:
        r = resolve(args.input, industry_hint=args.industry, name_hint=args.name)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
