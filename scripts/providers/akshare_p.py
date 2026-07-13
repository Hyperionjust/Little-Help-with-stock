"""akshare provider：A股/港股主力，美股接口兜底（免费）。

字段映射与已知坑见 references/data-sources.md。所有网络调用在 fetch_data 的 retry 包装内进行；
本文件只做「取数 + 字段标准化」，取不到就返回 None。
"""
from __future__ import annotations
from base import Provider, QUOTE_FIELDS


def _num(x):
    try:
        v = float(x)
        return None if v != v else v  # NaN → None
    except (TypeError, ValueError):
        return None


class AksharePorvider(Provider):
    name = "akshare"
    supports_markets = ("A", "HK", "US")

    def _ak(self):
        import akshare as ak
        return ak

    def get_quote(self, resolved):
        ak = self._ak()
        mk, sym = resolved["market"], resolved["symbol"]
        out = {k: None for k in QUOTE_FIELDS}
        if mk == "A":
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == sym]
            if not row.empty:
                r = row.iloc[0]
                out["price"] = _num(r.get("最新价"))
                out["market_cap"] = _num(r.get("总市值"))
                out["prev_close"] = _num(r.get("昨收"))
                out["float_shares"] = _num(r.get("流通股"))
        elif mk == "HK":
            df = ak.stock_hk_spot_em()
            row = df[df["代码"] == sym]
            if not row.empty:
                r = row.iloc[0]
                out["price"] = _num(r.get("最新价"))
                out["prev_close"] = _num(r.get("昨收"))
        return out

    def get_kline(self, resolved, adjust="qfq"):
        ak = self._ak()
        mk, sym = resolved["market"], resolved["symbol"]
        if mk == "A":
            df = ak.stock_zh_a_hist(symbol=sym, period="daily", adjust=adjust)
            cols = {"日期": "dates", "开盘": "open", "最高": "high", "最低": "low",
                    "收盘": "close", "成交量": "volume"}
        elif mk == "HK":
            df = ak.stock_hk_hist(symbol=sym, period="daily", adjust=adjust)
            cols = {"日期": "dates", "开盘": "open", "最高": "high", "最低": "low",
                    "收盘": "close", "成交量": "volume"}
        else:
            return {}
        out = {"adjust": adjust, "source": "akshare", "as_of": None}
        for src_c, dst in cols.items():
            if src_c in df.columns:
                out[dst] = [(_num(x) if dst != "dates" else str(x)) for x in df[src_c].tolist()]
        return out

    # 东财 datacenter 报表列名 → 标准化字段（实测 2026-07，见 data-sources.md）
    _BS_MAP = {"TOTAL_ASSETS": "total_assets", "TOTAL_LIABILITIES": "total_liabilities",
               "TOTAL_PARENT_EQUITY": "equity", "TOTAL_CURRENT_ASSETS": "current_assets",
               "TOTAL_CURRENT_LIAB": "current_liabilities", "INVENTORY": "inventory",
               "ACCOUNTS_RECE": "accounts_receivable", "MONETARYFUNDS": "cash"}
    _INC_MAP = {"TOTAL_OPERATE_INCOME": "revenue", "OPERATE_COST": "cogs",
                "OPERATE_PROFIT": "operating_income", "PARENT_NETPROFIT": "net_income",
                "INTEREST_EXPENSE": "interest_expense"}
    _CF_MAP = {"NETCASH_OPERATE": "ocf"}
    _DEBT_COLS = ["SHORT_LOAN", "LONG_LOAN", "BOND_PAYABLE", "LEASE_LIAB",
                  "NONCURRENT_LIAB_1YEAR"]

    def get_financials(self, resolved):
        """A股年报三表（东财 datacenter，免费）。金额 元→百万。最近财年在 index 0。"""
        if resolved["market"] != "A":
            return {}
        ak = self._ak()
        sym = resolved["symbol"]
        pre = "SH" if sym.startswith(("6", "9", "5")) else "SZ"
        em = f"{pre}{sym}"
        bs = ak.stock_balance_sheet_by_yearly_em(symbol=em)
        inc = ak.stock_profit_sheet_by_yearly_em(symbol=em)
        cf = ak.stock_cash_flow_sheet_by_yearly_em(symbol=em)
        if bs is None or bs.empty:
            return {}

        def rows_by_date(df, mapping):
            out = {}
            for _, r in df.iterrows():
                d = str(r.get("REPORT_DATE", ""))[:10]
                if not d:
                    continue
                rec = out.setdefault(d, {})
                for src, dst in mapping.items():
                    v = _num(r.get(src))
                    if v is not None:
                        rec[dst] = v / 1e6  # 元 → 百万
            return out

        b = rows_by_date(bs, self._BS_MAP)
        i = rows_by_date(inc, self._INC_MAP)
        c = rows_by_date(cf, self._CF_MAP)
        # 有息负债合计 + 有效税率
        for _, r in bs.iterrows():
            d = str(r.get("REPORT_DATE", ""))[:10]
            debt = sum((_num(r.get(col)) or 0) for col in self._DEBT_COLS)
            if d in b:
                b[d]["total_debt"] = debt / 1e6
        for _, r in inc.iterrows():
            d = str(r.get("REPORT_DATE", ""))[:10]
            tp, tax = _num(r.get("TOTAL_PROFIT")), _num(r.get("INCOME_TAX"))
            if d in i and tp and tax is not None and tp > 0:
                i[d]["effective_tax_rate"] = tax / tp

        annual = []
        for d in sorted(set(b) | set(i) | set(c), reverse=True):
            row = {"period": f"{d[:4]}FY", "report_date": d}
            row.update(b.get(d, {}))
            row.update(i.get(d, {}))
            row.update(c.get(d, {}))
            # 毛利
            if row.get("revenue") is not None and row.get("cogs") is not None:
                row["gross_profit"] = row["revenue"] - row["cogs"]
            annual.append(row)
        return {"source": "akshare.eastmoney_datacenter", "accounting_standard": "CAS",
                "annual": annual}
