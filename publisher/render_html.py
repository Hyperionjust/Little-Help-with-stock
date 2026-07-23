"""渲染交互式 HTML dashboard（单文件离线可开，ECharts CDN）。

关键：把完整 analysis JSON 以 <script id="analysis-data" type="application/json"> 内嵌，供
verify_consistency.py 机检对账。所有展示数字都从这个 JSON 取，保证与 JSON 完全一致。
用法：python render_html.py analysis.json [-o dashboard.html]
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'engine'))
import _paths  # noqa: F401,E402  路径引导，见 engine/_paths.py
from _util import load_json  # noqa: E402


def _v(node):
    return node.get("value") if isinstance(node, dict) else node


def _fmt(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, (int, float)):
        return f"{x:,.{nd}f}"
    return str(x)


def render(analysis):
    res = analysis.get("resolution", {})
    q = analysis.get("quote", {})
    val = analysis.get("valuation", {})
    pr = analysis.get("profitability", {})
    so = analysis.get("solvency", {})
    gr = analysis.get("growth", {})
    qr = analysis.get("quality_report", {})
    tech = analysis.get("technicals", {})
    is_pharma = res.get("is_pharma")
    ph = analysis.get("pharma", {})

    embedded = json.dumps(analysis, ensure_ascii=False)

    def card(label, node, unit=""):
        v = _v(node)
        src = node.get("source", "") if isinstance(node, dict) else ""
        formula = node.get("formula", "") if isinstance(node, dict) else ""
        tip = f"{src} | {formula}".replace('"', "'")
        return (f'<div class="card" title="{tip}"><div class="clabel">{label}</div>'
                f'<div class="cval">{_fmt(v)}{unit}</div></div>')

    val_cards = "".join([
        card("PE-TTM", val.get("pe_ttm", {}), "x"),
        card("PE静态", val.get("pe_static", {}), "x"),
        card("PB", val.get("pb", {}), "x"),
        card("PS-TTM", val.get("ps_ttm", {}), "x"),
        card("PEG", val.get("peg", {}), ""),
        card("EV/EBITDA", val.get("ev_ebitda", {}), "x"),
        card("股息率", val.get("dividend_yield", {}), "%"),
        card("市值", q.get("market_cap", {}), ""),
    ])
    prof_cards = "".join([
        card("ROE", pr.get("roe", {}), "%"),
        card("ROA", pr.get("roa", {}), "%"),
        card("ROIC", pr.get("roic", {}), "%"),
        card("毛利率", pr.get("gross_margin", {}), "%"),
        card("净利率", pr.get("net_margin", {}), "%"),
        card("现金含量", pr.get("cash_content", {}), "x"),
    ])
    solv_cards = "".join([
        card("资产负债率", so.get("debt_to_asset", {}), "%"),
        card("流动比率", so.get("current_ratio", {}), "x"),
        card("速动比率", so.get("quick_ratio", {}), "x"),
        card("应收天数", so.get("ar_days", {}), "天"),
        card("存货天数", so.get("inventory_days", {}), "天"),
    ])
    grow_cards = "".join([
        card("营收YoY", gr.get("revenue_yoy", {}), "%"),
        card("净利YoY", gr.get("net_income_yoy", {}), "%"),
        card("营收3年CAGR", gr.get("revenue_cagr_3y", {}), "%"),
        card("营收5年CAGR", gr.get("revenue_cagr_5y", {}), "%"),
    ])

    # 逆向验证红黄绿灯
    rv = analysis.get("reverse_validation", {})
    flags_html = ""
    for f in rv.get("flags", []):
        color = {"red": "#e74c3c", "yellow": "#f39c12", "green": "#27ae60"}.get(f.get("level"), "#888")
        flags_html += (f'<span class="flag" style="background:{color}">{f.get("name")}: '
                       f'{_fmt(f.get("value"))}</span>')

    # 门禁面板
    crit = qr.get("critical", [])
    warn = qr.get("warning", [])
    gate_class = "gate-bad" if qr.get("degraded") else ("gate-warn" if warn else "gate-ok")
    gate_status = "🔴 降级报告（存在 critical）" if qr.get("degraded") else (
        "🟡 通过（有警告）" if warn else "🟢 全部通过")
    gate_items = "".join(f'<li class="crit">CRITICAL · {c["check"]}: {c["message"]}</li>' for c in crit)
    gate_items += "".join(f'<li class="warn">WARN · {w["check"]}: {w["message"]}</li>' for w in warn)

    # 医药区块
    pharma_html = ""
    if is_pharma and ph:
        assets_rows = ""
        for a in ph.get("rnpv", {}).get("assets", []):
            assets_rows += (
                f"<tr><td>{a.get('asset')}</td><td>{a.get('indication')}</td>"
                f"<td>{a.get('current_phase')}</td><td>{_fmt(_v(a.get('cumulative_pos')),4)}</td>"
                f"<td>{_fmt(_v(a.get('peak_sales')),0)}</td><td>{_fmt(_v(a.get('asset_rnpv')),1)}</td></tr>")
        checklist_rows = ""
        for c in ph.get("human_verification_checklist", []):
            v = c.get("value")
            vs = _fmt(_v(v)) if isinstance(v, dict) else _fmt(v) if isinstance(v, (int, float)) else str(v)
            checklist_rows += (f"<tr><td>{c.get('item')}</td><td>{vs}</td>"
                               f"<td>{c.get('source_type')}</td><td>{c.get('impact_on_rnpv')}</td></tr>")
        dp = ph.get("double_penalty_check", {})
        pharma_html = f"""
        <div class="section">
          <h2>🧬 医药 rNPV 分析（范式：{ph.get('paradigm')}）</h2>
          <div class="row">
            {card("公司层 rNPV", ph.get("rnpv",{}).get("company_rnpv",{}), "")}
            {card("Σ管线rNPV", ph.get("rnpv",{}).get("sum_asset_rnpv",{}), "")}
            {card("净现金", ph.get("rnpv",{}).get("net_cash",{}), "")}
            {card("临床折现率", ph.get("clinical_discount_rate",{}), "")}
          </div>
          <p class="dp {'ok' if dp.get('passed') else 'bad'}">折现率不双重计罚检查：
            clinical_rate={dp.get('clinical_rate')} vs WACC={dp.get('wacc')} →
            {'✅ 通过（独立参数）' if dp.get('passed') else '❌ 失败（双重计罚）'}</p>
          <h3>分资产 rNPV 拆解</h3>
          <table><thead><tr><th>资产</th><th>适应症</th><th>当前阶段</th><th>累积PoS</th>
            <th>峰值销售(百万)</th><th>rNPV(百万)</th></tr></thead><tbody>{assets_rows}</tbody></table>
          <div id="rnpv_chart" style="height:320px"></div>
          <h3>临床管线看板（共 {ph.get('pipeline',{}).get('total_count')} 项，
            终止 {len(ph.get('pipeline',{}).get('terminated',[]))} 项）</h3>
          <div id="pipeline_chart" style="height:280px"></div>
          <h3 class="checklist-h">🔴 需人工核对清单</h3>
          <table class="checklist"><thead><tr><th>项目</th><th>取值</th><th>来源类型</th>
            <th>对rNPV影响</th></tr></thead><tbody>{checklist_rows}</tbody></table>
        </div>"""

    disclaimer = analysis.get("meta", {}).get("disclaimer", "")

    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{res.get('name','')} {res.get('symbol','')} · stock-metrics-pro</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script id="analysis-data" type="application/json">{embedded}</script>
<style>
/* Claude 原版深色风格：纯黑底 + Claude 橙 (#D97757) + 米白文字 */
:root{{--bg:#0d0d0d;--panel:#1a1a1a;--card:#1c1c1c;--fg:#f5f2ec;--muted:#8f8b84;
--accent:#D97757;--accent2:#e8926f;--line:#2a2a2a;--up:#e0685a;--down:#5a9e7a;
--ok:#5a9e7a;--warnc:#d9a44a;--bad:#d9635a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
padding:24px;line-height:1.55}}
h1{{font-size:22px;font-weight:600;margin:0}}
h1 small{{color:var(--muted);font-weight:400;font-size:13px}}
h2{{font-size:17px;font-weight:600;border-left:3px solid var(--accent);padding-left:10px;
margin:26px 0 10px}}
h3{{font-size:15px;font-weight:600;color:var(--accent2);margin:16px 0 8px}}
.header{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;
border-bottom:1px solid var(--line);padding-bottom:14px}}
.price{{font-size:30px;font-weight:700;color:var(--accent)}}
.row{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 16px;
min-width:120px;flex:1;transition:border-color .15s}}
.card:hover{{border-color:var(--accent)}}
.clabel{{font-size:12px;color:var(--muted)}}
.cval{{font-size:21px;font-weight:600;margin-top:5px;color:var(--fg)}}
.section{{margin:22px 0;background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:18px}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px}}
th,td{{border-bottom:1px solid var(--line);padding:8px 10px;text-align:right}}
th{{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.3px}}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}
tbody tr:hover{{background:#ffffff08}}
.checklist thead th{{background:#D9775718;color:var(--accent2)}}
.checklist-h{{color:var(--accent)}}
.flag{{display:inline-block;color:#0d0d0d;padding:3px 11px;border-radius:20px;font-size:12px;
margin:3px;font-weight:600}}
.gate{{padding:13px 17px;border-radius:12px;margin:14px 0;border:1px solid}}
.gate-ok{{background:#5a9e7a18;border-color:var(--ok)}}
.gate-warn{{background:#d9a44a18;border-color:var(--warnc)}}
.gate-bad{{background:#d9635a1f;border-color:var(--bad)}}
.gate b{{font-size:14px}}.gate ul{{margin:8px 0 0;padding-left:18px}}
.gate li{{margin:4px 0;font-size:13px}}
.crit{{color:var(--bad)}}.warn{{color:var(--warnc)}}
.dp{{margin:10px 0;font-size:13px}}.dp.ok{{color:var(--ok)}}.dp.bad{{color:var(--bad);font-weight:600}}
.disc{{color:var(--muted);font-size:12px;margin-top:28px;border-top:1px dashed var(--line);
padding-top:14px}}
::-webkit-scrollbar{{height:8px;width:8px}}::-webkit-scrollbar-thumb{{background:#333;border-radius:4px}}
</style></head><body>
<div class="header">
  <div><h1>{res.get('name','')} <small>{res.get('symbol','')} · {res.get('market')}股 · {res.get('exchange','')}</small></h1></div>
  <div class="price">{_fmt(_v(q.get('price')))} {res.get('currency','')}</div>
</div>
<div class="gate gate-{ 'bad' if qr.get('degraded') else ('warn' if warn else 'ok')}">
  <b>质量门禁：{gate_status}</b><ul>{gate_items or '<li>无异常</li>'}</ul></div>

<h2>估值</h2><div class="row">{val_cards}</div>
<div id="kline_chart" style="height:360px"></div>
<div id="macd_chart" style="height:180px"></div>
<div id="rsi_chart" style="height:180px"></div>
<h2>盈利质量</h2><div class="row">{prof_cards}</div>
<div id="dupont_chart" style="height:300px"></div>
<h2>偿债与营运</h2><div class="row">{solv_cards}</div>
<h2>成长</h2><div class="row">{grow_cards}</div>
<h2>逆向验证（红黄绿灯）</h2><div>{flags_html or '数据不足'}</div>
{pharma_html}
<div class="disc">{disclaimer}</div>

<script>
const DATA = JSON.parse(document.getElementById('analysis-data').textContent);
const V = n => (n && typeof n==='object') ? n.value : n;
// Claude 深色主题调色
const C={{fg:'#f5f2ec',muted:'#8f8b84',line:'#2a2a2a',grid:'#222',accent:'#D97757',
  accent2:'#e8926f',up:'#e0685a',down:'#5a9e7a',bar:'#D97757'}};
const AX={{axisLine:{{lineStyle:{{color:C.line}}}},axisLabel:{{color:C.muted,fontSize:11}},
  splitLine:{{lineStyle:{{color:C.grid}}}}}};
function baseOpt(title){{return{{backgroundColor:'transparent',
  title:{{text:title,textStyle:{{fontSize:13,color:C.fg,fontWeight:600}},left:0,top:2}},
  textStyle:{{color:C.fg}},tooltip:{{trigger:'axis',backgroundColor:'#1c1c1c',
    borderColor:C.line,textStyle:{{color:C.fg}}}},
  legend:{{textStyle:{{color:C.muted}},top:2,right:8}}}};}}
function line(id,title,cats,series){{
  const el=document.getElementById(id); if(!el) return;
  echarts.init(el).setOption(Object.assign(baseOpt(title),{{
    grid:{{left:52,right:20,top:34,bottom:28}},
    xAxis:Object.assign({{type:'category',data:cats||[],show:!!cats}},AX),
    yAxis:Object.assign({{type:'value',scale:true}},AX),series:series}}));
}}
const k=DATA.technicals||{{}};
const S=k.series||{{}};
const palette=['#D97757','#e8926f','#c9a227','#7a9e8e','#9e7a8e'];
// 真实K线蜡烛图 + MA20/MA60 叠加
(function(){{
  const el=document.getElementById('kline_chart'); if(!el||!S.candle) return;
  echarts.init(el).setOption(Object.assign(baseOpt('K线（前复权）+ MA20/MA60'),{{
    legend:{{data:['K线','MA20','MA60'],textStyle:{{color:C.muted}},top:2,right:8}},
    axisPointer:{{link:[{{xAxisIndex:'all'}}]}},
    grid:{{left:55,right:20,top:34,bottom:44}},
    xAxis:Object.assign({{type:'category',data:S.dates,axisLabel:{{show:false}}}},AX),
    yAxis:Object.assign({{type:'value',scale:true}},AX),
    dataZoom:[{{type:'inside'}},{{type:'slider',height:16,bottom:6,borderColor:C.line,
      fillerColor:'#D9775733',textStyle:{{color:C.muted}}}}],
    series:[
      {{name:'K线',type:'candlestick',data:S.candle,
        itemStyle:{{color:C.up,color0:C.down,borderColor:C.up,borderColor0:C.down}}}},
      {{name:'MA20',type:'line',data:S.ma20,smooth:true,showSymbol:false,
        lineStyle:{{width:1.2,color:C.accent}}}},
      {{name:'MA60',type:'line',data:S.ma60,smooth:true,showSymbol:false,
        lineStyle:{{width:1.2,color:'#c9a227'}}}}
    ]}}));
}})();
// MACD 时间序列副图
(function(){{
  const el=document.getElementById('macd_chart'); if(!el||!S.dif) return;
  echarts.init(el).setOption(Object.assign(baseOpt('MACD (DIF/DEA/柱)'),{{
    legend:{{data:['DIF','DEA','柱'],textStyle:{{color:C.muted}},top:2,right:8}},
    grid:{{left:55,right:20,top:34,bottom:24}},
    xAxis:Object.assign({{type:'category',data:S.dates,axisLabel:{{show:false}}}},AX),
    yAxis:Object.assign({{type:'value',scale:true}},AX),
    series:[
      {{name:'DIF',type:'line',data:S.dif,showSymbol:false,lineStyle:{{width:1.2,color:C.accent}}}},
      {{name:'DEA',type:'line',data:S.dea,showSymbol:false,lineStyle:{{width:1.2,color:'#c9a227'}}}},
      {{name:'柱',type:'bar',data:S.macd_hist,
        itemStyle:{{color:p=>p.data>=0?C.up:C.down}}}}
    ]}}));
}})();
// RSI14 时间序列副图
(function(){{
  const el=document.getElementById('rsi_chart'); if(!el||!S.rsi14) return;
  echarts.init(el).setOption(Object.assign(baseOpt('RSI(14)'),{{
    grid:{{left:55,right:20,top:34,bottom:24}},
    xAxis:Object.assign({{type:'category',data:S.dates,axisLabel:{{show:false}}}},AX),
    yAxis:Object.assign({{type:'value',min:0,max:100}},AX),
    series:[{{type:'line',data:S.rsi14,showSymbol:false,lineStyle:{{width:1.2,color:C.accent}},
      markLine:{{silent:true,lineStyle:{{color:C.muted,type:'dashed'}},
        data:[{{yAxis:70}},{{yAxis:30}}]}}}}]}}));
}})();
// 杜邦瀑布
const dp=(DATA.profitability||{{}}).dupont||{{}};
line('dupont_chart','杜邦分解: 净利率×资产周转×权益乘数 ≈ ROE',
  ['净利率','资产周转','权益乘数','乘积≈ROE'],
  [{{type:'bar',data:[V(dp.net_margin),V(dp.asset_turnover),V(dp.equity_multiplier),V(dp.product_check)],
    itemStyle:{{color:C.bar,borderRadius:[4,4,0,0]}}}}]);
// 医药图
if(DATA.pharma){{
  const assets=(DATA.pharma.rnpv||{{}}).assets||[];
  const pipe=assets.filter(a=>!a.marketed);
  const rc=document.getElementById('rnpv_chart');
  if(rc){{echarts.init(rc).setOption(Object.assign(baseOpt('分资产 rNPV（百万）'),{{
    grid:{{left:60,right:20,top:34,bottom:70}},
    xAxis:Object.assign({{type:'category',data:pipe.map(a=>(a.asset||'').slice(0,16)),
      axisLabel:{{color:C.muted,fontSize:10,rotate:35}}}},AX),
    yAxis:Object.assign({{type:'value'}},AX),
    series:[{{type:'bar',data:pipe.map(a=>V(a.asset_rnpv)),
      itemStyle:{{color:C.accent,borderRadius:[4,4,0,0]}}}}]}}));}}
  const trials=(DATA.pharma.pipeline||{{}}).trials||[];
  const pc=document.getElementById('pipeline_chart');
  if(pc){{const phases={{}};trials.forEach(t=>{{const p=t.phase||'未知';phases[p]=(phases[p]||0)+1;}});
    echarts.init(pc).setOption(Object.assign(baseOpt('管线阶段分布（试验数）'),{{
    grid:{{left:52,right:20,top:34,bottom:28}},
    xAxis:Object.assign({{type:'category',data:Object.keys(phases)}},AX),
    yAxis:Object.assign({{type:'value'}},AX),
    series:[{{type:'bar',data:Object.values(phases),
      itemStyle:{{color:'#7a9e8e',borderRadius:[4,4,0,0]}}}}]}}));}}
}}
window.addEventListener('resize',()=>document.querySelectorAll('div').forEach(d=>{{
  const c=echarts.getInstanceByDom(d); if(c) c.resize();}}));
</script></body></html>"""
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    analysis = load_json(args.analysis)
    html = render(analysis)
    out = args.out or f"{analysis.get('resolution', {}).get('symbol', 'out')}_dashboard.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(out)


if __name__ == "__main__":
    main()
