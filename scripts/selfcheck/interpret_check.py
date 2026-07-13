"""防幻觉检查：Markdown 看板"解读要点"里出现的数字必须存在于 JSON。

抽取解读文字里的全部数字，逐个在 JSON 值池中查存在性（容差匹配）。出现 JSON 中不存在的数字即 fail
——这是"LLM 只解读、不算术、不补数"的机检面，也是本 skill 与其他股票 skill 最本质的区别。

用法：interpret_check.py <analysis.json> <markdown.md>
可选：Markdown 中用 <!-- interpret:start --> / <!-- interpret:end --> 圈定解读段，只查该段。
退出码 0=无幻觉，1=发现 JSON 中不存在的数字。
"""
from __future__ import annotations
import argparse
import json
import re
import sys


# 图表/明细数组不算"可引用指标值"——否则解读里的幻觉数字可能巧合命中某个K线/RSI刻度，
# 削弱防幻觉守卫。解读只应引用 headline 指标，故这些键排除出值池。
_SKIP_KEYS = {"series", "stage_breakdown", "sensitivity", "_raw_inputs", "candle", "volume",
              "dates", "moving_averages", "trials", "terminated", "kline", "benchmark_kline"}


def collect_values(obj, out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for kk, v in obj.items():
            if kk in _SKIP_KEYS:
                continue
            collect_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_values(v, out)
    elif isinstance(obj, (int, float)):
        out.append(float(obj))
    return out


def num_in_pool(n, pool, rel=0.02, abs_tol=0.05):
    for p in pool:
        if abs(n - p) <= abs_tol:
            return True
        denom = max(abs(n), abs(p), 1e-9)
        if abs(n - p) / denom <= rel:
            return True
    return False


def extract_interpret_section(md):
    m = re.search(r"<!--\s*interpret:start\s*-->(.*?)<!--\s*interpret:end\s*-->", md, re.S)
    if m:
        return m.group(1)
    # 退而求其次：找"解读要点"标题后的内容
    m = re.search(r"(解读要点|解读|要点)(.*)$", md, re.S)
    return m.group(2) if m else md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("markdown")
    ap.add_argument("--whole", action="store_true", help="检查整篇而非仅解读段")
    args = ap.parse_args()
    with open(args.analysis, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(args.markdown, encoding="utf-8") as f:
        md = f.read()

    section = md if args.whole else extract_interpret_section(md)
    pool = collect_values(analysis)
    nums = [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d+", section)]

    hallucinated = []
    for n in nums:
        if not num_in_pool(n, pool):
            hallucinated.append(n)

    if hallucinated:
        print("HALLUCINATION: 以下解读数字在 JSON 中不存在（LLM 疑似自行算术/补数）:")
        for n in hallucinated:
            print("  -", n)
        sys.exit(1)
    print(f"CLEAN: 解读段 {len(nums)} 个数字全部可在 JSON 溯源")
    sys.exit(0)


if __name__ == "__main__":
    main()
