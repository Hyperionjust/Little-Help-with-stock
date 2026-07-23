"""深度研报图表生成（matplotlib → SVG，内嵌 PDF）。

为什么用 matplotlib SVG 而非 Mermaid：
  审计发现原素材把 Mermaid 图交给 WeasyPrint 渲染，而 WeasyPrint 不执行 JS，
  Mermaid 源码直接漏进 PDF（原版智谱研报第 11-12 页可见 `style P2 fill:#003366`
  这类未渲染的 mermaid 语法）。matplotlib 在服务端出静态 SVG，WeasyPrint 直接
  嵌入，无此问题——这是"唯一引擎 + 无 JS 依赖"决策的直接收益。

所有图的数据来自 analysis.json ∪ model.json，图上任何数字都能被闸3对账。
输出 SVG 字符串，模板用 <img src="data:image/svg+xml;base64,..."> 内嵌。
"""
from __future__ import annotations
import base64
import io

_OK = True
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    # 注册 CJK 字体（沙箱有 Noto CJK）
    for fp in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
               "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]:
        try:
            font_manager.fontManager.addfont(fp)
        except Exception:
            pass
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["axes.facecolor"] = "#fdfcf5"     # 绘图区也用淡黄底
    plt.rcParams["figure.facecolor"] = "#fdfcf5"
except ImportError:
    _OK = False

PAGE_BG = "#fdfcf5"    # 与 report.css body 背景一致，图表融进页面不留白块

# 新低饱和色系（与 report.css 一致：藏蓝/低饱和橙绿红 + 暖灰网格）
NAVY = "#1f3a5f"       # 藏蓝主色
LIGHTBLUE = "#6b8cae"  # 淡蓝辅助
FORECAST = "#c8925a"   # 低饱和橙
GREEN = "#5a8a6a"      # 低饱和绿
RED = "#b5615a"        # 低饱和红
GRID = "#e6e3d8"       # 暖灰网格（配淡黄底）


def _svg(fig):
    buf = io.StringIO()
    # facecolor=页面淡黄底：图表画布融进页面，不在淡黄页上显白块
    fig.savefig(buf, format="svg", bbox_inches="tight",
                facecolor=PAGE_BG, edgecolor="none")
    plt.close(fig)
    raw = buf.getvalue().encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode()


def available():
    return _OK


def revenue_margin_chart(hist_years, hist_rev, proj_years, proj_rev,
                         hist_margin, proj_margin):
    """营收柱（历史实心+预测描边）+ 利润率线（右轴）。"""
    if not _OK:
        return None
    fig, ax1 = plt.subplots(figsize=(6.2, 3.0))
    years = hist_years + proj_years
    rev = hist_rev + proj_rev
    x = range(len(years))
    colors = [NAVY] * len(hist_years) + ["none"] * len(proj_years)
    edges = [NAVY] * len(years)
    ax1.bar(x, rev, color=colors, edgecolor=edges, linewidth=1.3, hatch=[""] * len(hist_years) + ["///"] * len(proj_years))
    ax1.set_ylabel("营业收入（百万）", fontsize=8, color=NAVY)
    ax1.tick_params(axis="y", labelsize=7)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(years, fontsize=7, rotation=0)
    ax1.grid(axis="y", color=GRID, linewidth=0.5)

    ax2 = ax1.twinx()
    margins = hist_margin + proj_margin
    mx_h = range(len(hist_margin))
    mx_f = range(len(hist_margin) - 1, len(years)) if proj_margin else []
    ax2.plot(list(mx_h), [m * 100 if m else None for m in hist_margin],
             color=RED, marker="o", ms=3, lw=1.5, label="营业利润率")
    if proj_margin:
        ax2.plot(list(mx_f), [(hist_margin[-1:] + proj_margin)[i] * 100
                              if (hist_margin[-1:] + proj_margin)[i] else None
                              for i in range(len(mx_f))],
                 color=RED, marker="o", ms=3, lw=1.5, ls="--")
    ax2.set_ylabel("营业利润率（%）", fontsize=8, color=RED)
    ax2.tick_params(axis="y", labelsize=7)
    fig.suptitle("营收与利润率趋势（实心=历史，斜纹=预测）", fontsize=9, color=NAVY, y=1.02)
    return _svg(fig)


def ufcf_chart(years, ufcf):
    """自由现金流柱：正绿负红。"""
    if not _OK:
        return None
    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    colors = [GREEN if v is not None and v >= 0 else RED for v in ufcf]
    ax.bar(range(len(years)), [v or 0 for v in ufcf], color=colors)
    ax.axhline(0, color="#888", lw=0.6)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years, fontsize=7)
    ax.set_ylabel("UFCF（百万）", fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    fig.suptitle("无杠杆自由现金流（预测期）", fontsize=9, color=NAVY, y=1.0)
    return _svg(fig)


def scenario_chart(price, bull, base, bear):
    """情景对比横条 + 现价参考线。"""
    if not _OK:
        return None
    fig, ax = plt.subplots(figsize=(6.2, 2.2))
    labels, vals, colors = [], [], []
    for lab, v, c in [("悲观", bear, RED), ("基准", base, NAVY), ("乐观", bull, GREEN)]:
        if v is not None:
            labels.append(lab); vals.append(v); colors.append(c)
    y = range(len(labels))
    ax.barh(list(y), vals, color=colors, height=0.5)
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:,.0f}", va="center", fontsize=8)
    if price:
        ax.axvline(price, color=FORECAST, lw=1.4, ls="--")
        ax.text(price, len(labels) - 0.3, f"现价 {price:,.0f}", color=FORECAST,
                fontsize=7.5, ha="center")
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("每股价值", fontsize=8); ax.tick_params(axis="x", labelsize=7)
    fig.suptitle("情景估值 vs 现价", fontsize=9, color=NAVY, y=1.02)
    return _svg(fig)


def sensitivity_heatmap(rows, cols, matrix, row_label="WACC", col_label="永续g"):
    """WACC×g 敏感性热力图。None 格留白。"""
    if not _OK:
        return None
    import numpy as np
    m = np.array([[v if v is not None else np.nan for v in r] for r in matrix], dtype=float)
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    im = ax.imshow(m, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(cols))); ax.set_xticklabels([f"{c:.1%}" for c in cols], fontsize=7)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([f"{r:.1%}" for r in rows], fontsize=7)
    ax.set_xlabel(col_label, fontsize=8); ax.set_ylabel(row_label, fontsize=8)
    for i in range(len(rows)):
        for j in range(len(cols)):
            if not np.isnan(m[i, j]):
                ax.text(j, i, f"{m[i,j]:,.0f}", ha="center", va="center", fontsize=6.5)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04).ax.tick_params(labelsize=6)
    fig.suptitle("敏感性：每股价值", fontsize=9, color=NAVY, y=1.0)
    return _svg(fig)


def tornado_chart(assumptions):
    """假设龙卷风：按 ±每股影响排序的横条。"""
    if not _OK:
        return None
    items = [(a["label"], a.get("per_share_impact")) for a in assumptions
             if a.get("per_share_impact")]
    items.sort(key=lambda x: x[1])
    if not items:
        return None
    fig, ax = plt.subplots(figsize=(6.2, max(2.0, 0.4 * len(items))))
    labels = [i[0] for i in items]
    vals = [i[1] for i in items]
    ax.barh(range(len(labels)), vals, color=NAVY, height=0.6)
    for i, v in enumerate(vals):
        ax.text(v, i, f" ±{v:,.0f}", va="center", fontsize=7.5)
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("每股价值敏感度（绝对值）", fontsize=8); ax.tick_params(axis="x", labelsize=7)
    fig.suptitle("假设龙卷风图", fontsize=9, color=NAVY, y=1.02)
    return _svg(fig)


def rnpv_waterfall(assets):
    """医药：各管线 rNPV 贡献瀑布。"""
    if not _OK or not assets:
        return None
    names = [a.get("asset", "?")[:12] for a in assets]
    vals = [(a.get("asset_rnpv") or {}).get("value", 0) if isinstance(a.get("asset_rnpv"), dict)
            else a.get("asset_rnpv", 0) for a in assets]
    fig, ax = plt.subplots(figsize=(6.2, 2.8))
    ax.bar(range(len(names)), vals, color=NAVY)
    ax.set_xticks(range(len(names))); ax.set_xticklabels(names, fontsize=6.5, rotation=30, ha="right")
    ax.set_ylabel("rNPV（百万）", fontsize=8); ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    fig.suptitle("各管线资产 rNPV 贡献", fontsize=9, color=NAVY, y=1.0)
    return _svg(fig)
