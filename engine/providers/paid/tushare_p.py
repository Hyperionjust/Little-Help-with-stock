"""tushare provider（预留）：检测到环境变量 TUSHARE_TOKEN 时自动升级为 A股 Tier-0。

体现「付费/高级源只需加一个 provider 文件，不改计算层」的设计。无 token 时 available()=False，
fetch_data 跳过它，降级链退回 akshare。
"""
from __future__ import annotations
import os
from _util import normalize_unit
from base import Provider, QUOTE_FIELDS


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class TushareProvider(Provider):
    name = "tushare"
    supports_markets = ("A",)

    @staticmethod
    def available():
        return bool(os.environ.get("TUSHARE_TOKEN"))

    def _pro(self):
        import tushare as ts
        ts.set_token(os.environ["TUSHARE_TOKEN"])
        return ts.pro_api()

    def _ts_code(self, resolved):
        sym = resolved["symbol"]
        suf = "SH" if sym.startswith(("6", "9")) else "SZ"
        return f"{sym}.{suf}"

    def get_quote(self, resolved):
        pro = self._pro()
        out = {k: None for k in QUOTE_FIELDS}
        try:
            df = pro.daily_basic(ts_code=self._ts_code(resolved), fields="close,total_mv,total_share,float_share")
            if df is not None and len(df):
                r = df.iloc[0]
                out["price"] = _num(r.get("close"))
                out["market_cap"] = normalize_unit(_num(r.get("total_mv")), "万元")  # 修：原版×1e4得到元，错1e6倍
                out["total_shares"] = _num(r.get("total_share"))
                out["float_shares"] = _num(r.get("float_share"))
        except Exception:
            pass
        return out
