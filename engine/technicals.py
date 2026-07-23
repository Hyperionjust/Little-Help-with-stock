"""Technical indicator library over raw_data['kline'] (qfq/前复权 arrays).

kline schema: {adjust, source, as_of, dates[], open[], high[], low[], close[], volume[]}
所有指标输出 av() 三件套。定义关系（MACD/杜邦等）由 test_metrics 的性质断言守护。
"""
from __future__ import annotations
import math
from _util import av, ema, wilder, sma, stddev, safe_div, pct


def _k(raw):
    return raw.get("kline", {})


def compute_technicals(raw):
    k = _k(raw)
    close = k.get("close", [])
    high = k.get("high", [])
    low = k.get("low", [])
    vol = k.get("volume", [])
    src = k.get("source", "unknown")
    as_of = k.get("as_of")
    adjust = k.get("adjust", "unknown")
    out = {}
    if not close:
        return {"_note": "no kline"}

    # 均线系统
    mas = {}
    for n in (5, 10, 20, 60, 120, 250):
        mas[f"MA{n}"] = av(sma(close, n), src, f"最近{n}日收盘均值", as_of=as_of)
    out["moving_averages"] = mas
    align = _ma_alignment([mas[f"MA{n}"]["value"] for n in (5, 10, 20, 60)])
    out["ma_alignment"] = av(align, src, "MA5>MA10>MA20>MA60多头；反之空头；否则纠缠", as_of=as_of)

    # MACD (12,26,9)，柱用2倍口径
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    dif = [(a - b) if (a is not None and b is not None) else None for a, b in zip(e12, e26)]
    dif_valid = [x for x in dif if x is not None]
    dea_valid = ema(dif_valid, 9) if dif_valid else []
    dif_last = dif[-1] if dif else None
    dea_last = dea_valid[-1] if dea_valid else None
    hist_last = (2 * (dif_last - dea_last)) if (dif_last is not None and dea_last is not None) else None
    out["macd"] = {
        "dif": av(dif_last, src, "EMA12-EMA26", as_of=as_of),
        "dea": av(dea_last, src, "EMA9(DIF)", as_of=as_of),
        "hist": av(hist_last, src, "2×(DIF-DEA)", as_of=as_of),
    }

    # RSI 6/12/24 (Wilder)
    rsis = {}
    diffs = [close[i] - close[i - 1] for i in range(1, len(close))]
    gains = [max(d, 0) for d in diffs]
    losses = [max(-d, 0) for d in diffs]
    for n in (6, 12, 24):
        ag = wilder(gains, n)
        al = wilder(losses, n)
        val = None
        if ag and al and ag[-1] is not None and al[-1] is not None:
            if al[-1] < 1e-12:
                val = 100.0
            else:
                rs = ag[-1] / al[-1]
                val = 100 - 100 / (1 + rs)
        rsis[f"RSI{n}"] = av(val, src, f"Wilder RSI({n})", as_of=as_of)
    out["rsi"] = rsis

    # KDJ (9,3,3)
    out["kdj"] = _kdj(high, low, close, src, as_of)

    # BIAS
    bias = {}
    for n in (6, 12, 24):
        m = sma(close, n)
        bias[f"BIAS{n}"] = av(pct(safe_div(close[-1] - m, m)) if m else None, src,
                              f"(C-MA{n})/MA{n}×100", as_of=as_of)
    out["bias"] = bias

    # ATR(14)
    trs = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        trs.append(tr)
    atrw = wilder(trs, 14)
    out["atr"] = av(atrw[-1] if atrw and atrw[-1] is not None else None, src,
                    "Wilder ATR(14); TR=max(H-L,|H-Cp|,|L-Cp|)", as_of=as_of)

    # 20日年化波动率
    if len(close) >= 21:
        rets = [math.log(close[i] / close[i - 1]) for i in range(len(close) - 20, len(close))]
        sd = stddev(rets)
        vol20 = (sd * math.sqrt(252)) if sd is not None else None
    else:
        vol20 = None
    out["volatility_20d"] = av(pct(vol20) if vol20 is not None else None, src,
                               "std(日对数收益,20)×√252，年化", as_of=as_of, unit="%")

    # 量比 / 换手率
    vr = None
    if len(vol) >= 6:
        base = sum(vol[-6:-1]) / 5
        vr = safe_div(vol[-1], base)
    out["volume_ratio"] = av(vr, src, "今日量/过去5日均量", as_of=as_of)
    float_shares = (raw.get("quote", {}).get("float_shares"))
    tr_rate = safe_div(vol[-1], float_shares) if (vol and float_shares) else None
    out["turnover_rate"] = av(pct(tr_rate) if tr_rate is not None else None, src,
                              "成交量/流通股本", as_of=as_of, unit="%")

    # 相对强弱
    out["relative_strength"] = _relative_strength(raw, close, src, as_of)

    # 支撑压力（近120日分位数法）
    out["support_resistance"] = _support_resistance(low, high, close, src, as_of)
    out["_adjust_mode"] = adjust
    # 图表序列（供 HTML 画真实K线+MACD+RSI，截取近 N 点）
    out["series"] = _build_series(k, e12, e26, dif, dea_valid, gains, losses, n_tail=120)
    return out


def _rolling_ma(series, n):
    out = [None] * len(series)
    for i in range(n - 1, len(series)):
        out[i] = sum(series[i - n + 1:i + 1]) / n
    return out


def _build_series(k, e12, e26, dif, dea_valid, gains, losses, n_tail=120):
    """构造图表用时间序列（K线蜡烛 + MA叠加 + MACD + RSI），截取尾部 n_tail 点。"""
    dates = k.get("dates", [])
    o, h, l, c, v = (k.get("open", []), k.get("high", []), k.get("low", []),
                     k.get("close", []), k.get("volume", []))
    if not c:
        return {}
    ma20 = _rolling_ma(c, 20)
    ma60 = _rolling_ma(c, 60)
    # DEA 对齐回原始长度
    dea_full = [None] * len(c)
    idx_valid = [i for i, x in enumerate(dif) if x is not None]
    for j, i in enumerate(idx_valid):
        if j < len(dea_valid) and dea_valid[j] is not None:
            dea_full[i] = dea_valid[j]
    hist = [None] * len(c)
    for i in range(len(c)):
        if dif[i] is not None and dea_full[i] is not None:
            hist[i] = 2 * (dif[i] - dea_full[i])
    # RSI14 序列（Wilder）
    ag = wilder(gains, 14)
    al = wilder(losses, 14)
    rsi14 = [None] * len(c)
    for i in range(1, len(c)):
        a, b = ag[i - 1], al[i - 1]
        if a is not None and b is not None:
            rsi14[i] = 100.0 if b < 1e-12 else (100 - 100 / (1 + a / b))

    def tail(x):
        return x[-n_tail:] if len(x) >= n_tail else x

    return {
        "dates": tail(dates),
        "candle": [[oo, cc, ll, hh] for oo, cc, ll, hh in
                   zip(tail(o), tail(c), tail(l), tail(h))],  # ECharts candlestick: [open,close,low,high]
        "volume": tail(v),
        "ma20": tail(ma20), "ma60": tail(ma60),
        "dif": tail(dif), "dea": tail(dea_full), "macd_hist": tail(hist),
        "rsi14": tail(rsi14),
    }


def _ma_alignment(vals):
    if any(v is None for v in vals):
        return "数据不足"
    a, b, c, d = vals
    if a > b > c > d:
        return "多头排列"
    if a < b < c < d:
        return "空头排列"
    return "均线纠缠"


def _kdj(high, low, close, src, as_of):
    n = 9
    if len(close) < n:
        return {"k": av(None, src, "KDJ数据不足"), "d": av(None, src, ""), "j": av(None, src, "")}
    rsv = []
    for i in range(n - 1, len(close)):
        window_h = max(high[i - n + 1:i + 1])
        window_l = min(low[i - n + 1:i + 1])
        denom = window_h - window_l
        rsv.append(0.0 if denom < 1e-12 else (close[i] - window_l) / denom * 100)
    # K=EMA(RSV,3) with 50 seed (通达信习惯), D=EMA(K,3)
    k_prev, d_prev = 50.0, 50.0
    ks, ds = [], []
    for r in rsv:
        k_prev = (2 * k_prev + r) / 3
        d_prev = (2 * d_prev + k_prev) / 3
        ks.append(k_prev)
        ds.append(d_prev)
    k, d = ks[-1], ds[-1]
    j = 3 * k - 2 * d
    return {"k": av(k, src, "K=EMA(RSV,3),RSV=(C-Ln)/(Hn-Ln)×100,n=9", as_of=as_of),
            "d": av(d, src, "D=EMA(K,3)", as_of=as_of),
            "j": av(j, src, "J=3K-2D", as_of=as_of)}


def _relative_strength(raw, close, src, as_of):
    bench = raw.get("benchmark_kline", {})
    bclose = bench.get("close", [])
    bname = bench.get("name", raw.get("resolution", {}).get("benchmark_index", "基准"))
    out = {"benchmark": bname}
    for n in (20, 60, 120):
        if len(close) > n and len(bclose) > n:
            stock_ret = close[-1] / close[-1 - n] - 1
            bench_ret = bclose[-1] / bclose[-1 - n] - 1
            out[f"excess_{n}d"] = av(pct(stock_ret - bench_ret), src,
                                     f"个股{n}日收益 - {bname}{n}日收益", as_of=as_of, unit="%")
        else:
            out[f"excess_{n}d"] = av(None, src, f"{n}日相对强弱数据不足", as_of=as_of, unit="%")
    return out


def _support_resistance(low, high, close, src, as_of):
    window = min(120, len(close))
    if window < 5:
        return {"support": av(None, src, "数据不足"), "resistance": av(None, src, "")}
    lows = sorted(low[-window:])
    highs = sorted(high[-window:])
    q20 = lows[int(0.20 * (len(lows) - 1))]
    q80 = highs[int(0.80 * (len(highs) - 1))]
    return {
        "support": av(q20, src, f"近{window}日最低价20分位", as_of=as_of),
        "resistance": av(q80, src, f"近{window}日最高价80分位", as_of=as_of),
        "last_close": av(close[-1], src, "最新收盘", as_of=as_of),
    }
