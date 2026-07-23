"""腾讯行情 provider（qt.gtimg.cn，免费无 key）：A/港/美 实时报价 + 市值。

接口返回 ~ 分隔字符串：f[1]=名称 f[3]=现价 f[4]=昨收 f[45]=总市值(亿)。
轻量、稳定、几乎不被风控——作 quote 的高优先级源与跨源价格校验源。
"""
from __future__ import annotations
from _util import normalize_unit
from base import Provider, QUOTE_FIELDS

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _num(x):
    try:
        v = float(x)
        return None if (v != v or v == 0) else v  # NaN/0 → None
    except (TypeError, ValueError):
        return None


class TencentProvider(Provider):
    name = "tencent"
    supports_markets = ("A", "HK", "US")

    def _code(self, resolved):
        mk, sym = resolved["market"], resolved["symbol"]
        if mk == "A":
            pre = "sh" if sym.startswith(("6", "9", "5")) else "sz"
            return f"{pre}{sym}"
        if mk == "HK":
            return f"hk{sym.zfill(5)}"
        return f"us{sym.upper()}"

    def get_quote(self, resolved):
        import requests
        out = {k: None for k in QUOTE_FIELDS}
        code = self._code(resolved)
        r = requests.get(f"https://qt.gtimg.cn/q={code}", headers=UA, timeout=10)
        r.raise_for_status()
        txt = r.text
        if '="' not in txt or "pv_none" in txt:
            return out
        f = txt.split("~")
        if len(f) < 6:
            return out
        out["_name"] = f[1] or None   # 证券名称（供 resolution 补名）
        out["price"] = _num(f[3])
        out["prev_close"] = _num(f[4])
        # f[45]=总市值(亿) → 百万
        if len(f) > 45:
            mcap_yi = _num(f[45])
            if mcap_yi:
                out["market_cap"] = normalize_unit(mcap_yi, "亿元")
        return out

    def get_kline(self, resolved, adjust="qfq"):
        """腾讯 fqkline（web.ifzq.gtimg.cn，前/后复权，避开被封的 eastmoney push2his）。"""
        import requests
        code = self._code(resolved)
        fq = {"qfq": "qfq", "hfq": "hfq", "none": ""}.get(adjust, "qfq")
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param={code},day,,,600,{fq}")
        r = requests.get(url, headers=UA, timeout=12)
        r.raise_for_status()
        js = r.json()
        node = (js.get("data", {}) or {}).get(code, {})
        rows = node.get(f"{fq}day") or node.get("day") or []
        if not rows:
            return {}
        out = {"adjust": adjust if fq else "none", "source": "tencent.fqkline",
               "as_of": rows[-1][0] if rows else None,
               "dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        for row in rows:
            # row: [date, open, close, high, low, volume, ...]
            out["dates"].append(row[0])
            out["open"].append(_num(row[1])); out["close"].append(_num(row[2]))
            out["high"].append(_num(row[3])); out["low"].append(_num(row[4]))
            out["volume"].append(_num(row[5]) if len(row) > 5 else None)
        return out
