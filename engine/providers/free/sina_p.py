"""新浪行情 provider（hq.sinajs.cn，免费无 key）：A股实时报价。

价格专用轻量源，主要用途是给跨源价格校验提供第二个独立读数。需带 Referer。
返回：var hq_str_sh600519="名称,今开,昨收,现价,最高,最低,..."
"""
from __future__ import annotations
from base import Provider, QUOTE_FIELDS

HDR = {"Referer": "https://finance.sina.com.cn",
       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _num(x):
    try:
        v = float(x)
        return None if (v != v or v == 0) else v  # NaN/0 → None
    except (TypeError, ValueError):
        return None


class SinaProvider(Provider):
    name = "sina"
    supports_markets = ("A", "HK")

    def _code(self, resolved):
        mk, sym = resolved["market"], resolved["symbol"]
        if mk == "A":
            pre = "sh" if sym.startswith(("6", "9", "5")) else "sz"
            return f"{pre}{sym}"
        return f"rt_hk{sym.zfill(5)}"

    def get_quote(self, resolved):
        import requests
        out = {k: None for k in QUOTE_FIELDS}
        code = self._code(resolved)
        r = requests.get(f"https://hq.sinajs.cn/list={code}", headers=HDR, timeout=10)
        r.raise_for_status()
        txt = r.text
        if '="' not in txt:
            return out
        payload = txt.split('="', 1)[1].rstrip('";\n')
        f = payload.split(",")
        if resolved["market"] == "A" and len(f) > 3:
            out["price"] = _num(f[3])
            out["prev_close"] = _num(f[2])
        elif resolved["market"] == "HK" and len(f) > 6:
            # rt_hk: 英文名,中文名,今开,昨收,最高,最低,现价
            out["price"] = _num(f[6])
            out["prev_close"] = _num(f[3])
        return out
