"""Yahoo provider：美股主力、港股兜底。双路径：yfinance 库优先，requests 直连兜底。

为什么要双路径：yfinance 新版底层用 curl_cffi，在部分代理环境会 SSL 握手失败；而雅虎的
v8 chart（K线/现价）与 fundamentals-timeseries（年报）两个公开端点用普通 requests 就能拿到。
库失败时自动切直连，字段口径一致。金额统一转百万。
"""
from __future__ import annotations
import time

from base import Provider, QUOTE_FIELDS

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# fundamentals-timeseries 年报字段映射（雅虎 type → 标准化字段）
TS_TYPES = {
    "annualTotalRevenue": "revenue",
    "annualCostOfRevenue": "cogs",
    "annualGrossProfit": "gross_profit",
    "annualOperatingIncome": "operating_income",
    "annualNetIncomeCommonStockholders": "net_income",
    "annualTotalAssets": "total_assets",
    "annualTotalLiabilitiesNetMinorityInterest": "total_liabilities",
    "annualStockholdersEquity": "equity",
    "annualCurrentAssets": "current_assets",
    "annualCurrentLiabilities": "current_liabilities",
    "annualInventory": "inventory",
    "annualAccountsReceivable": "accounts_receivable",
    "annualCashAndCashEquivalents": "cash",
    "annualTotalDebt": "total_debt",
    "annualInterestExpense": "interest_expense",
    "annualOperatingCashFlow": "ocf",
    "annualDepreciationAndAmortization": "depreciation",
}


def _num(x):
    try:
        v = float(x)
        return None if v != v else v  # NaN → None
    except (TypeError, ValueError):
        return None


class YfinanceProvider(Provider):
    name = "yfinance"
    supports_markets = ("US", "HK")

    def _symbol(self, resolved):
        sym = resolved["symbol"]
        # 指数/已带交易所后缀的代码（^GSPC, 000300.SS, 0700.HK）原样透传
        if sym.startswith("^") or "." in sym:
            return sym
        if resolved["market"] == "HK":
            return f"{sym.lstrip('0').zfill(4)}.HK"
        return sym

    # ---------- 库路径 ----------
    def _lib_quote(self, resolved):
        import yfinance as yf
        t = yf.Ticker(self._symbol(resolved))
        fi = t.fast_info
        return {"price": _num(fi.get("last_price")),
                "prev_close": _num(fi.get("previous_close")),
                "market_cap": (_num(fi.get("market_cap")) or 0) / 1e6 or None,
                "total_shares": _num(fi.get("shares")),
                "float_shares": None}

    # ---------- 直连路径 ----------
    def _chart(self, resolved, rng="2y"):
        import requests
        sym = self._symbol(resolved)
        u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
        r = requests.get(u, headers=UA, timeout=15)
        r.raise_for_status()
        return r.json()["chart"]["result"][0]

    def _timeseries(self, resolved, extra_types=""):
        import requests
        sym = self._symbol(resolved)
        p2 = int(time.time())
        p1 = p2 - 6 * 365 * 86400
        types = ",".join(TS_TYPES.keys()) + ",quarterlyShareIssued" + extra_types
        u = (f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
             f"timeseries/{sym}?symbol={sym}&type={types}&period1={p1}&period2={p2}")
        r = requests.get(u, headers=UA, timeout=20)
        r.raise_for_status()
        return r.json().get("timeseries", {}).get("result", [])

    def get_quote(self, resolved):
        out = {k: None for k in QUOTE_FIELDS}
        try:
            out.update(self._lib_quote(resolved))
            if out.get("price") is not None:
                return out
        except Exception:
            pass
        # 直连：chart meta 给现价/昨收；市值 = 现价 × 最新已发行股本(ShareIssued)
        js = self._chart(resolved, rng="5d")
        meta = js.get("meta", {})
        out["price"] = _num(meta.get("regularMarketPrice"))
        out["prev_close"] = _num(meta.get("chartPreviousClose"))
        try:
            res = self._timeseries(resolved)
            shares = None
            for x in res:
                if x["meta"]["type"][0] == "quarterlyShareIssued":
                    vals = [v for v in (x.get("quarterlyShareIssued") or []) if v]
                    if vals:
                        shares = _num(vals[-1]["reportedValue"]["raw"])
            if shares and out["price"]:
                out["total_shares"] = shares
                out["market_cap"] = out["price"] * shares / 1e6  # 百万
        except Exception:
            pass
        return out

    def get_kline(self, resolved, adjust="qfq"):
        # 库路径
        try:
            import yfinance as yf
            h = yf.Ticker(self._symbol(resolved)).history(period="2y", auto_adjust=True)
            if h is not None and not h.empty:
                return {"adjust": "qfq", "source": "yfinance.history", "as_of": None,
                        "dates": [str(d.date()) for d in h.index],
                        "open": [_num(x) for x in h["Open"]],
                        "high": [_num(x) for x in h["High"]],
                        "low": [_num(x) for x in h["Low"]],
                        "close": [_num(x) for x in h["Close"]],
                        "volume": [_num(x) for x in h["Volume"]]}
        except Exception:
            pass
        # 直连：v8 chart，adjclose 调整（近似前复权）
        js = self._chart(resolved, rng="2y")
        ts = js.get("timestamp", [])
        ind = js.get("indicators", {})
        q = (ind.get("quote") or [{}])[0]
        adj = (ind.get("adjclose") or [{}])[0].get("adjclose")
        close_raw = q.get("close", [])
        # 用 adjclose/close 比例调整 OHLC（前复权）
        rows = {"dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        import datetime as _dt
        for i, t in enumerate(ts):
            c = close_raw[i] if i < len(close_raw) else None
            if c is None:
                continue
            ratio = (adj[i] / c) if (adj and adj[i] is not None and c) else 1.0
            rows["dates"].append(_dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"))
            rows["open"].append(_num(q["open"][i] * ratio) if q.get("open") and q["open"][i] is not None else None)
            rows["high"].append(_num(q["high"][i] * ratio) if q.get("high") and q["high"][i] is not None else None)
            rows["low"].append(_num(q["low"][i] * ratio) if q.get("low") and q["low"][i] is not None else None)
            rows["close"].append(_num(c * ratio))
            rows["volume"].append(_num(q["volume"][i]) if q.get("volume") and q["volume"][i] is not None else None)
        if not rows["close"]:
            return {}
        return {"adjust": "qfq", "source": "yahoo.chart_direct", "as_of": rows["dates"][-1], **rows}

    def get_financials(self, resolved):
        # 直连 fundamentals-timeseries（库路径同样依赖它，直接用 requests 更稳）
        try:
            res = self._timeseries(resolved)
        except Exception:
            return {}
        by_date = {}
        for x in res:
            ty = x["meta"]["type"][0]
            field = TS_TYPES.get(ty)
            if not field:
                continue
            for v in (x.get(ty) or []):
                if not v:
                    continue
                d = v.get("asOfDate")
                raw = ((v.get("reportedValue") or {}).get("raw"))
                if d and raw is not None:
                    by_date.setdefault(d, {})[field] = raw / 1e6  # → 百万
        if not by_date:
            return {}
        annual = []
        for d in sorted(by_date.keys(), reverse=True):
            row = {"period": f"FY{d[:4]}", "report_date": d}
            row.update(by_date[d])
            annual.append(row)
        std = "US_GAAP" if resolved["market"] == "US" else "IFRS"
        return {"source": "yahoo.fundamentals_timeseries", "accounting_standard": std,
                "annual": annual}
