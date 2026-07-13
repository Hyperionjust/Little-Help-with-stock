# 四种输出格式规范

`{symbol}_analysis.json` 是唯一数据源，其余三种从它渲染，数字必须完全一致（verify_consistency 机检）。

## 1. JSON（`{symbol}_analysis.json`）

结构见 `references/analysis_schema.json`。要点：每个关键数字是 `{value, source, as_of, formula[, unit,
period]}` 三件套；医药假设额外带 `source_type`；含 `quality_report`、`data_gaps`；医药标的含 `pharma` 段。

## 2. Chat 内 Markdown 看板（由 LLM 从 JSON 撰写）

**铁律**：解读要点里出现的每个数字都必须已存在于 JSON（interpret_check 机检）。用 `<!-- interpret:start -->`
/ `<!-- interpret:end -->` 包裹解读段以便机检。模板：

```markdown
## {name}（{symbol} · {market}股）分析看板

**现价** {price} {currency} ｜ **市值** {market_cap} ｜ 数据源 {source} · {as_of}

### 质量门禁：{🟢通过 / 🟡有警告 / 🔴降级}
{逐条列 critical/warning，若降级则醒目提示"以下为降级报告"}

### 核心指标
| 维度 | 指标 | 值 |
|---|---|---|
| 估值 | PE-TTM / PB / PS / EV-EBITDA | ... |
| 盈利 | ROE / 净利率 / 现金含量 | ... |
| 成长 | 营收YoY / 3年CAGR | ... |
| 偿债 | 资产负债率 / 流动比率 | ... |

### 逆向验证
{应收/存货 vs 营收增速、OCF-NI 背离，红黄绿灯}

<!-- interpret:start -->
### 解读要点（只引用上表出现的数字）
1. ...
2. ...
（3–5 条）
<!-- interpret:end -->

{医药标的额外：}
### 🧬 rNPV 分部小结
公司层 rNPV {值}（范式 {paradigm}）= Σ管线 {值} + 净现金 {值} − 债务 {值}；折现率 {clinical_rate}（独立，未双重计罚）
| 资产 | 适应症 | 阶段 | 累积PoS | rNPV |
...

### 🔴 需人工核对清单
| 项目 | 取值 | 来源类型 | 对rNPV影响 |
...（所有 user_assumption 逐条）

---
*{disclaimer}*
```

## 3. HTML dashboard（render_html.py）

单文件离线可开，ECharts(CDN)。**必须**内嵌 `<script id="analysis-data" type="application/json">{完整JSON}
</script>`（机检对账依赖它）。通用区块：均线快照/MACD/RSI、估值卡、杜邦、比率、逆向验证红黄绿灯、门禁面板。
医药区块：rNPV 分资产瀑布、管线阶段分布、需人工核对清单表。中文界面，`prefers-color-scheme` 深浅自适应。

## 4. Excel 底稿（render_xlsx.py）

- `Raw Data` sheet：原始数据（price/market_cap + TTM 财务原始量），带数据源列。
- `Ratios` sheet：**活公式**，B 列是 `='Raw Data'!Bn/...` 引用，绝不硬编码数值（verify_consistency 强制）。
- `rNPV` sheet（医药）：管线逐行，PoS/峰值销售/折现率为可编辑输入格，rNPV 用公式 `=峰值×0.35×PoS×现值系数`
  实时重算——手动情景分析主战场。
- `需人工核对清单` sheet（医药）+ `质量门禁` sheet。
