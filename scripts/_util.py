"""Shared helpers: annotated values, safe math, timestamps.

零心算原则的地基：所有数字都通过 av() 带上 (source, as_of, formula)。
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _denan(x):
    """NaN → None。NaN 绝不应进入输出（非法 JSON、污染下游、破坏一致性对账）。"""
    if isinstance(x, float) and x != x:
        return None
    return x


def av(value, source, formula, as_of=None, unit=None, period=None, **extra):
    """Build an annotated value (三件套). value 允许 None（缺数）。NaN 自动归 None。"""
    d = {"value": _denan(value), "source": source, "as_of": as_of, "formula": formula}
    if unit is not None:
        d["unit"] = unit
    if period is not None:
        d["period"] = period
    d.update(extra)
    return d


def pharma_av(value, source, formula, source_type, as_of=None, unit=None, **extra):
    """Annotated value for pharma assumptions, carrying source_type."""
    assert source_type in ("hard", "benchmark", "user_assumption"), source_type
    d = av(value, source, formula, as_of=as_of, unit=unit, **extra)
    d["source_type"] = source_type
    return d


def safe_div(a, b):
    """None-safe, NaN-safe division. Returns None if inputs missing/NaN or denom ~ 0."""
    a, b = _denan(a), _denan(b)
    if a is None or b is None:
        return None
    try:
        if abs(b) < 1e-12:
            return None
        r = a / b
        return _denan(r)
    except (TypeError, ZeroDivisionError):
        return None


def pct(x):
    """Fraction -> percentage number (0.12 -> 12.0). None-safe."""
    return None if x is None else x * 100.0


def avg2(a, b):
    """Average of two, tolerant of a missing endpoint (falls back to the present one)."""
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2.0


def ema(series, span, seed_sma=True):
    """Exponential moving average, list-in list-out (same length; leading Nones until seeded).

    span: EMA period; alpha = 2/(span+1). 首值用前 span 个的 SMA 作种子（A股/通达信习惯）。
    """
    n = len(series)
    out = [None] * n
    if n == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    if seed_sma:
        if n < span:
            return out
        seed = sum(series[:span]) / span
        out[span - 1] = seed
        prev = seed
        for i in range(span, n):
            prev = alpha * series[i] + (1 - alpha) * prev
            out[i] = prev
    else:
        prev = series[0]
        out[0] = prev
        for i in range(1, n):
            prev = alpha * series[i] + (1 - alpha) * prev
            out[i] = prev
    return out


def wilder(series, span):
    """Wilder's smoothing (used by RSI/ATR). Same-length output with leading Nones."""
    n = len(series)
    out = [None] * n
    if n < span:
        return out
    seed = sum(series[:span]) / span
    out[span - 1] = seed
    prev = seed
    for i in range(span, n):
        prev = (prev * (span - 1) + series[i]) / span
        out[i] = prev
    return out


def sma(series, n):
    """Trailing simple moving average value at the last point (or None if insufficient)."""
    if len(series) < n:
        return None
    return sum(series[-n:]) / n


def stddev(series):
    m = len(series)
    if m < 2:
        return None
    mean = sum(series) / m
    var = sum((x - mean) ** 2 for x in series) / (m - 1)
    return math.sqrt(var)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
