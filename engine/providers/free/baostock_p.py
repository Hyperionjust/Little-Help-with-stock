"""baostock provider：A股历史数据兜底（免费）。价格/K线兜底，无市值接口。"""
from __future__ import annotations
from base import Provider


def _num(x):
    try:
        v = float(x)
        return None if v != v else v  # NaN → None
    except (TypeError, ValueError):
        return None


class BaostockProvider(Provider):
    name = "baostock"
    supports_markets = ("A",)

    def _code(self, resolved):
        sym = resolved["symbol"]
        pre = "sh" if sym.startswith(("6", "9")) else "sz"
        return f"{pre}.{sym}"

    def get_kline(self, resolved, adjust="qfq"):
        import baostock as bs
        flag = {"qfq": "2", "hfq": "1", "none": "3"}.get(adjust, "2")
        lg = bs.login()
        if lg.error_code != "0":
            return {}
        try:
            rs = bs.query_history_k_data_plus(
                self._code(resolved), "date,open,high,low,close,volume",
                frequency="d", adjustflag=flag)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
        finally:
            bs.logout()
        if not rows:
            return {}
        out = {"adjust": adjust, "source": "baostock", "as_of": None,
               "dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        for r in rows:
            out["dates"].append(r[0])
            out["open"].append(_num(r[1])); out["high"].append(_num(r[2]))
            out["low"].append(_num(r[3])); out["close"].append(_num(r[4]))
            out["volume"].append(_num(r[5]))
        return out

    def get_quote(self, resolved):
        k = self.get_kline(resolved)
        if k.get("close"):
            return {"price": k["close"][-1], "prev_close": k["close"][-2] if len(k["close"]) > 1 else None,
                    "market_cap": None, "float_shares": None, "total_shares": None}
        return {}
