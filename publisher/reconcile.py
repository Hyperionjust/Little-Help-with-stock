"""闸3：全文数字对账（方案 §3.4）——interpret_check 的全文升级版。

最终报告（HTML/Markdown）里的每个财务数字，必须能在 analysis.json ∪ model.json
的值池中找到（±2% 容差）。找不到的即为**孤儿数字**，阻断交付。

这是本产品相对同类工具的核心差异：别家靠提示词要求模型别编数字，
这里靠机器逐个核对。

相对 interpret_check 的三点升级（方案阶段1-5）：
  1. 值池从单一 analysis.json 扩到 analysis ∪ model，并生成**换算变体**——
     内部口径是"百万"，但行文会写"1.947 万亿"、"19,470 亿"、"22.6%"，
     这些合法变形不应误报。
  2. 扫描范围从"解读段"扩到整份文档（HTML 剥标签与内嵌脚本后的可见文本 / MD 全文）。
  3. findings/--strict/退出码骨架（借鉴 xtt validate_deck_contract.py 的工程形态），
     每条发现带上下文片段，可精确定位到原句。

白名单哲学：**按上下文豁免，不按数值豁免。** 年份、日期、图表编号、章节号、
"前5大客户"这类叙述性数字按其上下文模式豁免；绝不因为"这个数看着无害"就放行——
那是对账闸失守的开始。发现误报应补上下文模式，禁止把具体数值塞进白名单。

用法：
  python reconcile.py <report.html|report.md> --analysis a.json [--model m.json]
                      [--strict] [--json] [--max-findings 50]
退出码：0=全部可溯源  1=存在孤儿数字  2=输入错误
"""
from __future__ import annotations
import argparse
import json
import re
import sys

# ── 值池排除键（继承 interpret_check 的已验证判断）─────────────────────
# 图表/明细数组不算"可引用指标值"——否则幻觉数字可能巧合命中某根K线，削弱守卫。
_SKIP_KEYS = {"series", "stage_breakdown", "_raw_inputs", "candle", "volume",
              "dates", "trials", "terminated", "kline", "benchmark_kline",
              "stages_ms"}
# 注意：interpret_check 排除了 sensitivity；此处**不再排除**——敏感性矩阵的格值
# 会被写进研报正文，属于合法引用，且矩阵值来自脚本计算（模型层契约保证）。


def collect_pool(*objs):
    """递归收集数值 + 生成换算变体。返回排序后的列表（供二分查找）。"""
    base = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _SKIP_KEYS:
                    continue
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            f = float(o)
            if f == f:  # 排 NaN
                base.append(f)

    for o in objs:
        if o:
            walk(o)

    pool = set()
    for v in base:
        pool.add(v)
        pool.add(round(v, 2))
        av = abs(v)
        # 比率的百分数形态：0.226 ↔ 22.6；1.848 ↔ 184.8（占比可以超过 100%——
        # 寒武纪 TV/EV=185% 教训：原来只对 <1.0 生成 ×100 变体，超百比率漏了）
        if av < 20.0:
            pool.add(v * 100)
        if 1.0 <= av <= 100.0:
            pool.add(v / 100)
        # 金额的单位变形：内部"百万" ↔ 行文的 亿 / 万亿 / 原始元
        if av >= 100:
            pool.add(v / 100)        # 百万 → 亿
            pool.add(v / 1e6)        # 百万 → 万亿
            pool.add(v * 100)        # 防"万"口径行文
    # 负数的绝对值形态，对**全部变体**统一取负——
    # 修复记录（茅台速览自检暴露）：原来只对原始值加 -v，派生变体（÷100 的亿口径）
    # 没有负形态。净负债 -51,464 百万显示为 "(514.6) 亿"，括号吞掉负号后
    # 正则读到 +514.6，值池里只有 -514.64，被误判孤儿。
    # 会计括号负数是常态表达，正负形态必须对称覆盖。
    pool |= {-x for x in pool}
    return sorted(pool)


def tolerance_for(token, rel=0.002):
    """按 token 的**显示精度**推导容差——这是本模块最关键的设计。

    容差的唯一目的是覆盖报告里的四舍五入，不是"差不多就算对"。
    固定容差做不到这一点，因为它同时要满足两个互斥需求：
      · 报告写 "57%"（真值 57.14%）→ 需要 ±0.5 才不误报
      · 报告写 "18.7%"（真值 19.0%）→ 需要 <0.3 才能拦下编造

    解法：容差 = 该 token 自己声明的精度。写几位小数，就按几位定容差。
      "57"    → 0 位 → ±0.5
      "18.7"  → 1 位 → ±0.05   → 与 19.0 差 0.3，拦下 ✅
      "22.58" → 2 位 → ±0.005

    踩坑记录：最初沿用 interpret_check 的固定 rel=2%，编造的 "18.7%" 命中了
    值池里真实存在的 19.0（相对差 1.58% < 2%）而漏网。对 10-100 量级的
    财务比率，2% 有 ±0.2~2.0 的宽度，足够让一个完全不同的数蒙混过关。

    相对容差 0.2% 保留，仅用于大数的量级舍入（1,947,000 → 194.7万）。
    单位换算由 collect_pool 的变体负责，不靠容差兜。
    """
    t = token.replace(",", "").lstrip("-")
    decimals = len(t.split(".")[1]) if "." in t else 0
    return 0.5 * (10 ** -decimals) + 1e-9, rel


def num_in_pool(n, sorted_pool, abs_tol=0.051, rel=0.002):
    """容差匹配。二分加速。abs_tol 由调用方按 token 精度传入（见 tolerance_for）。"""
    import bisect
    i = bisect.bisect_left(sorted_pool, n)
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(sorted_pool):
            p = sorted_pool[j]
            if abs(n - p) <= abs_tol:
                return True
            denom = max(abs(n), abs(p), 1e-9)
            if abs(n - p) / denom <= rel:
                return True
    return False


# ── HTML → 可见文本 ────────────────────────────────────────────────────

def html_visible_text(html):
    """剥掉 script/style（内嵌 analysis JSON 不属于'读者可见数字'）与标签。"""
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    # HTML 实体最常见的几个
    for ent, ch in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")]:
        html = html.replace(ent, ch)
    return html


# ── 上下文白名单（按模式豁免，不按数值豁免）────────────────────────────
# 每条 = (名称, 正则)。命中即豁免该数字。模式必须描述"为什么这不是财务数字"。
_ZH_COUNT_SUFFIX = r"(?:年|月|日|号|个|家|条|项|页|步|次|轮|批|只|款|名|位|大|季度?|阶段|部分|章|节|天|小时|分钟)"
WHITELIST_PATTERNS = [
    ("四位年份", re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)(?:\s*(?:年|FY|E|A|财年))?")),
    ("年份区间后段", re.compile(r"(?:19[5-9]\d|20[0-4]\d)\s*[-–—~至]\s*(\d{2,4})(?!\d)")),
    ("ISO日期", re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")),
    ("中文日期", re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日?")),
    ("图表编号", re.compile(r"(?:图表|图|表|Exhibit|Figure|Table|Chart)\s*\d+", re.I)),
    ("章节编号", re.compile(r"(?:§|第|Section|Part|Step|Phase|阶段|任务|Task)\s*\d+(?:\.\d+)*", re.I)),
    # 研报章节标题/目录的编号（"11 数据来源"、"8. 假设清单"）。只豁免"编号+具体章节名"，
    # 章节名白名单精确到实际标题词，绝不误伤财务数字（如"11 万元"不会命中）。
    ("研报章节编号", re.compile(
        r"\d{1,2}[.、]?\s*(?:执行摘要|六维|财务历史|财务质量|五年|财务投影|DCF|"
        r"估值|情景|敏感性|假设清单|可比公司|投资逻辑|多空|风险评估|医药|"
        r"管线|数据来源|溯源|免责|目录)")),
    # 补充研究章节 ID（S1·公司概览 … S6·催化剂）——结构编号，非数据
    ("补充研究节ID", re.compile(r"[Ss]\d(?=\s*[·.、\s])")),
    # 数字区间带单位（3-5 年 / 12-24 个月 / 80%-90% / 7nm）——区间/技术规格表达，
    # 属claim级而非单点数据；claim整体的引用由紧邻的 [n] 覆盖。
    ("数字区间", re.compile(r"\d{1,3}\s*%?\s*[-–—~]\s*\d{1,3}\s*(?:年|个?月|%|nm|倍|片|万|亿)")),
    ("制程规格", re.compile(r"\d{1,2}\s*nm")),
    ("枚举小数量", re.compile(r"(?:前|近|过去|未来|第|连续|共|约)\s*\d{1,2}\s*" + _ZH_COUNT_SUFFIX)),
    ("裸小数量词", re.compile(r"(?<![\d.,%])\d{1,2}\s*" + _ZH_COUNT_SUFFIX)),
    ("临床期数", re.compile(r"(?:Phase|期|[ⅠⅡⅢ])\s*[1-4]\b|[1-4]\s*期", re.I)),
    ("代码类", re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)|\b\d{4,5}\.HK|\b[A-Z]{1,5}\.[A-Z]{1,2}\b")),
    # 药物/产品代号：字母前缀 + 数字（SHR-1701、SHR-A1811、AK-104、BGB-3245）。
    # 连字符会让数字正则把 -1701 误当负数，故连字符也纳入豁免范围。
    ("药物产品代号", re.compile(r"[A-Z]{2,5}[-‐-―]?[A-Z]?\d{2,5}\b", re.I)),
    ("裸证券代码", re.compile(r"(?<![\d.])[063]\d{5}(?![\d.])|(?<![\d.])0\d{4}(?![\d.])")),
    ("52周惯用语", re.compile(r"52\s*(?:周|week|W)", re.I)),
    ("K线窗口参数", re.compile(r"(?:MA|RSI|KDJ|BIAS|ATR|MACD)\s*\(?\d{1,3}", re.I)),
    ("技术指标窗口", re.compile(r"\d{1,3}\s*日(?:均线|波动|支撑|压力|涨跌)")),
    ("版本号", re.compile(r"\bv?\d+\.\d+\.\d+\b")),
    ("时间戳时分", re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")),
    ("页码", re.compile(r"(?:第|Page|p\.)\s*\d+\s*(?:页|/)?", re.I)),
    ("免责阈值惯用语", re.compile(r"(?:超过|不足|至少|最多)\s*\d{1,3}\s*%")),
    ("比较符阈值", re.compile(r"[><≥≤>＜]\s*\d{1,3}\s*%")),   # (>80%) 这类规则常量
]

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _whitelisted_spans(text):
    spans = []
    for name, pat in WHITELIST_PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), name))
    return spans


def _in_spans(start, end, spans):
    for s, e, name in spans:
        if start >= s and end <= e:
            return name
    return None


def scan(text, pool_sorted, max_findings=50):
    """返回 (findings, stats)。findings 条目含上下文片段，可精确定位原句。"""
    spans = _whitelisted_spans(text)
    findings, checked, exempt = [], 0, 0
    for m in _NUM_RE.finditer(text):
        tok = m.group(0).rstrip(".")
        if not tok or tok in ("-",):
            continue
        wl = _in_spans(m.start(), m.end(), spans)
        if wl:
            exempt += 1
            continue
        try:
            n = float(tok.replace(",", ""))
        except ValueError:
            continue
        checked += 1
        atol, rtol = tolerance_for(tok)
        if not num_in_pool(n, pool_sorted, abs_tol=atol, rel=rtol):
            ctx = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
            findings.append({
                "severity": "critical",
                "code": "RECONCILE_ORPHAN_NUMBER",
                "value": n,
                "location": ctx.strip(),
            })
            if len(findings) >= max_findings:
                break
    return findings, {"checked": checked, "exempt": exempt,
                      "orphans": len(findings), "pool_size": len(pool_sorted)}


# ── 双分区对账（用户需求：量化段对账契约，研究段要求引用）───────────────
# 报告用 <!-- INSIGHT-BEGIN --> ... <!-- INSIGHT-END --> 标记联网研究分区。
# 量化区（标记外）：每个数字必须在契约值池里——严格对账。
# 研究区（标记内）：数字来自联网研究，无法对契约；改为要求每个数字附近有引用标记 [n]，
#   且每个 [n] 能在来源表解析。两种证据等级，两套核验规则——这正是"分开呈现"的技术落地。
_CITE_RE = re.compile(r"\[\d+\]")
INSIGHT_BEGIN = "INSIGHT-BEGIN"
INSIGHT_END = "INSIGHT-END"


def split_regions(html):
    """按注释标记切出 (量化区文本, 研究区文本)。研究区不存在时后者为空串。"""
    import re as _re
    text_full = html_visible_text(html)
    # 在可见文本里标记会被 html_visible_text 的 <!-- --> 剥除，故先在 html 上切
    m = _re.search(r"<!--\s*%s\s*-->(.*?)<!--\s*%s\s*-->" % (INSIGHT_BEGIN, INSIGHT_END),
                   html, _re.S)
    if not m:
        return text_full, ""
    insight_html = m.group(1)
    # 只核验研究**正文**，来源表（URL/日期是引用落点，非正文声明）切除
    body_end = insight_html.find("<!-- INSIGHT-BODY-END -->")
    if body_end >= 0:
        insight_html = insight_html[:body_end]
    rigorous_html = html[:m.start()] + html[m.end():]
    return html_visible_text(rigorous_html), html_visible_text(insight_html)


def scan_insight(text, n_sources, max_source_ref=None):
    """研究区核验：每个数字附近须有引用标记 [n]；每个 [n] 须 1..n_sources。

    返回 (findings, stats)。findings 里 code 区分 UNCITED_NUMBER / BAD_SOURCE_REF。
    """
    findings = []
    spans = _whitelisted_spans(text)
    checked = 0
    for m in _NUM_RE.finditer(text):
        tok = m.group(0).rstrip(".")
        if not tok or tok == "-":
            continue
        if _in_spans(m.start(), m.end(), spans):
            continue
        # 引用标记本身（[1][2]）不算待核验数字
        around = text[max(0, m.start() - 2):m.end() + 2]
        if "[" in around and "]" in around and around.strip().strip("[]").isdigit():
            continue
        checked += 1
        window = text[max(0, m.start() - 60): m.end() + 60]
        if not _CITE_RE.search(window):
            findings.append({"severity": "critical", "code": "UNCITED_NUMBER",
                             "value": tok, "location": window.strip()[:90]})
    # 引用编号越界检查
    for m in _CITE_RE.finditer(text):
        idx = int(m.group(0).strip("[]"))
        if idx < 1 or idx > n_sources:
            findings.append({"severity": "critical", "code": "BAD_SOURCE_REF",
                             "value": idx, "location": f"引用 [{idx}] 超出来源表 1..{n_sources}"})
    return findings, {"checked": checked, "n_sources": n_sources}


def reconcile(report_path, analysis, model=None, max_findings=50):
    with open(report_path, encoding="utf-8") as f:
        content = f.read()
    text = html_visible_text(content) if report_path.lower().endswith((".html", ".htm")) else content
    pool = collect_pool(analysis, model)
    return scan(text, pool, max_findings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="report.html 或 report.md")
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="将统计异常（豁免率过高等）也视为失败")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--max-findings", type=int, default=50)
    args = ap.parse_args()

    try:
        analysis = json.load(open(args.analysis, encoding="utf-8"))
        model = json.load(open(args.model, encoding="utf-8")) if args.model else None
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] 输入读取失败: {e}", file=sys.stderr)
        sys.exit(2)

    findings, stats = reconcile(args.report, analysis, model, args.max_findings)

    if args.as_json:
        print(json.dumps({"findings": findings, "stats": stats}, ensure_ascii=False, indent=2))
    else:
        if findings:
            print(f"[FAIL] 发现 {len(findings)} 个孤儿数字（无法溯源到契约 JSON）：")
            for f0 in findings[:20]:
                print(f"  {f0['value']}  …{f0['location'][:70]}…")
            if len(findings) > 20:
                print(f"  ...另有 {len(findings)-20} 个")
        print(f"[stats] 检查 {stats['checked']} 个数字，豁免 {stats['exempt']} 个（上下文白名单），"
              f"值池 {stats['pool_size']}")

    # 豁免率哨兵：>60% 说明白名单在吞噬对账面，--strict 下视为失败
    total = stats["checked"] + stats["exempt"]
    if args.strict and total > 20 and stats["exempt"] / total > 0.60:
        print(f"[FAIL --strict] 豁免率 {stats['exempt']/total:.0%} >60%，白名单可能过宽")
        sys.exit(1)

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
