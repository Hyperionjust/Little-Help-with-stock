"""efinance provider：A股/港股备选（免费）。字段级 fallback 的第二顺位。"""
from __future__ import annotations
from _util import normalize_unit
from base import Provider, QUOTE_FIELDS


def _num(x):
    try:
        v = float(x)
        return None if v != v else v  # NaN → None
    except (TypeError, ValueError):
        return None


class EfinanceProvider(Provider):
    name = "efinance"
    supports_markets = ("A", "HK")

    def get_quote(self, resolved):
        import efinance as ef
        out = {k: None for k in QUOTE_FIELDS}
        try:
            info = ef.stock.get_base_info(resolved["symbol"])
        except Exception:
            return out
        # get_base_info 返回 Series，字段名依接口
        try:
            out["price"] = _num(info.get("最新价"))
            out["market_cap"] = normalize_unit(_num(info.get("总市值")), "元")  # 修：原版未换算
        except Exception:
            pass
        return out

    def get_kline(self, resolved, adjust="qfq"):
        import efinance as ef
        fqt = {"qfq": 1, "hfq": 2, "none": 0}.get(adjust, 1)
        df = ef.stock.get_quote_history(resolved["symbol"], fqt=fqt)
        if df is None or len(df) == 0:
            return {}
        m = {"日期": "dates", "开盘": "open", "最高": "high", "最低": "low",
             "收盘": "close", "成交量": "volume"}
        out = {"adjust": adjust, "source": "efinance", "as_of": None}
        for s, d in m.items():
            if s in df.columns:
                out[d] = [(str(x) if d == "dates" else _num(x)) for x in df[s].tolist()]
        return out
