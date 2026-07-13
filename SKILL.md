---
name: stock-metrics-pro
description: >-
  分析 A股/港股/美股个股的财务比率与技术趋势，医药/生物科技标的额外做 rNPV 风险调整估值、临床管线、
  专利悬崖(LOE)与催化剂分析，生成带完整数据溯源的 JSON/Markdown/HTML/Excel 四格式报告。当用户提到任何股票
  代码（如 600519 / 00700 / AAPL / 0700.HK）、公司名+分析、财务比率、估值、PE/PB/ROE、技术指标、K线、
  MACD/RSI、rNPV、管线、临床试验、专利悬崖、"看一下某某股票"、"帮我分析下这只股票"、"这公司值多少钱"时，
  必须使用本 skill——即使用户没有明确说"分析"二字。本 skill 只做数据分析与呈现，不给买卖建议。
---

# stock-metrics-pro — 个股财务与技术分析（含医药 rNPV 专业模块）

这是一个**数据分析与呈现层**工具：获取行情与财务数据 → 用脚本计算财务比率/趋势指标/医药估值 → 以
JSON / Markdown / HTML / Excel 四种形态呈现，每个数字都可溯源。**本 skill 不做买卖建议**（建议层是未来的
独立扩展，本版只为其预留稳定的 JSON 接口）。

## ⚖️ 三条不可妥协的铁律

**1. 零心算原则。** 任何出现在最终输出中的数字——比率、指标、涨跌幅、CAGR、rNPV、累积 PoS——**必须由
Python 脚本计算产出**。你（LLM）只负责解读脚本输出的结构化 JSON，绝不自己读网页数字做算术、绝不估算、绝不
"补全"缺失数字。为什么：这类工具最大的信任杀手就是模型顺手心算错一个比率却讲得头头是道；把算术全部关进脚本，
错误就变成可复现、可测试、可审计的，而不是随机幻觉。缺数就如实标 `data_gaps`，不要脑补。

**2. 三件套标注。** 每个关键数字附带三样东西：(a) 数据源名称、(b) 采集时间戳、(c) 计算公式/口径。在 JSON 里
是字段，在 HTML/Excel/Markdown 里是脚注或悬浮提示。医药模块的每个假设（PoS、峰值销售、折现率、渗透率）额外
标 `source_type`：`hard`（数据库/公告）/ `benchmark`（行业基准）/ `user_assumption`（需人工确认）。为什么：
估值的分歧几乎全在假设上，把假设的来源等级摊开，读者才能判断该信多少。

**3. 质量门禁。** 报告生成前必须跑 `scripts/quality_gate.py` 的机械自查（见下）。critical 级不通过时**不得
输出正式报告**，只能输出带醒目警告的降级报告。为什么：一份看起来完整、实则价格取错源或财报过期一年的报告，比
没有报告更危险。

## 🔄 标准工作流

对任何个股分析请求，按顺序执行（全部通过脚本，你只在最后解读）：

```
1. 解析代码/名称   python scripts/resolve_symbol.py "<用户输入>"
                   → {market, symbol, name, currency, exchange, industry_tag, is_pharma}
2. 取数（降级链）  python scripts/fetch_data.py <resolved.json>  [--offline-fixture <path>]
                   → raw_data.json（字段级 fallback + data_gaps + 每字段 provider 标注）
3. 通用计算        python scripts/compute_metrics.py <raw_data.json>
                   → 估值/盈利质量/逆向验证/偿债/成长/技术面 指标块
4. 医药计算(条件)  若 is_pharma: python scripts/pharma_valuation.py <raw_data.json>
                   → rNPV 分解 / 管线看板 / 敏感性 / LOE / 催化剂 / 需人工核对清单
5. 质量门禁        python scripts/quality_gate.py <analysis.json>
                   → quality_report（critical/warning）写回 JSON；critical 则强制降级
6. 四格式渲染      python scripts/render_html.py <analysis.json>
                   python scripts/render_xlsx.py <analysis.json>
                   （JSON 直接落盘；Markdown 看板由你从 JSON 撰写，见下）
```

一步到位入口：`python scripts/run_analysis.py "<用户输入>" [--offline-fixture <path>]`，它串起
1–6 并写出 `{symbol}_analysis.json` / `{symbol}_dashboard.html` / `{symbol}_workbook.xlsx` 三个文件，
然后把 JSON 路径回给你，你据此写 Markdown 看板。

### 你（LLM）在流程里只做两件事

- **解读**：基于 `{symbol}_analysis.json` 写 3–5 条要点。铁律：**你引用的每个数字都必须已存在于 JSON 中**
  （`scripts/selfcheck/interpret_check.py` 会机检这一点）。不确定就不写，绝不为了行文顺畅编一个数。
- **呈现 Markdown 看板**：核心指标表 + 质量门禁状态 + 解读要点；医药标的额外附 rNPV 分部小结 + 🔴需人工核对
  清单。模板见 `references/output-templates.md`。

## 市场与代码解析

支持 A股 / 港股 / 美股。`resolve_symbol.py` 识别：`600519`、`sh600519`、`600519.SH`、`000001.SZ`、
中文名"贵州茅台"；`00700`、`hk00700`、`0700.HK`、"腾讯控股"；`AAPL`、`105.AAPL`、"Apple"。同时判定**行业
标签**（申万医药生物 / GICS Health Care / 港股医疗保健），命中则激活医药模块。判定逻辑与映射表在
`references/data-sources.md`。

## 数据层（免费为主，付费源预留）

Provider 抽象 + 分级降级链，代码在 `scripts/providers/`，配置与坑表在 `references/data-sources.md`：

- A股：腾讯/新浪(行情) → akshare-东财datacenter(财报) → efinance → baostock；有 `TUSHARE_TOKEN` 时 tushare 升 Tier-0
- 港股：腾讯/新浪 → yahoo → akshare
- 美股：yahoo(行情+财报直连) → 腾讯
- 医药临床：ClinicalTrials.gov v2（管线/阶段，用英文 sponsor 检索）+ openFDA（批准/标签），均免费无需 key

关键设计：**字段级 fallback**——主源拿到价格但缺市值，只对缺失字段调备选源补全，不整块切源。快而稳的行情源
（腾讯/新浪/雅虎）在前、重接口（akshare 财报）在后；跨源价格校验集齐 2 读数即早停。每字段记录实际来源。所有
请求带超时 + 指数退避重试，失败进 `data_gaps`。新增付费源（Tushare Pro/iFind/Wind/Cortellis/Evaluate）只需加
一个 provider 文件，不动计算层——这是抽象基类 `base.py` 的全部意义。

**本地数据导入**（`scripts/import_local.py`）：用户有 iFind/同花顺/Wind 导出的财报（xlsx/csv）时，中文/英文
字段别名自动映射成标准 raw_data（标 Tier-0），`--complement-live` 用免费源补现价/K线/临床。计算/门禁/渲染完全
复用。详见 README 第六、七节（含移植所需 domain allowlist 清单）。

## 计算层

全部计算在脚本，输出结构化 JSON。**SKILL.md 里不出现任何让你手算的公式。** 精确口径见
`references/metrics-formulas.md`。

- **基本面**（`compute_metrics.py`）：估值(PE-TTM/静态/预测、PB、PS、PEG、EV/EBITDA、股息率、市值)、
  盈利质量(ROE + 杜邦三分解、ROA、ROIC、毛利/净利率、现金含量)、**逆向验证**(应收 vs 营收增速、存货 vs 营收
  增速、经营现金流与净利润 3 年背离度——揭示"利润好看现金流恶化")、偿债营运、成长(YoY/QoQ/3年5年CAGR)。
  **PE 默认口径 = TTM**（静态/预测同时算并列出）。
- **技术面**（`compute_metrics.py`）：MA5/10/20/60/120/250 多空排列、MACD、RSI(6/12/24)、KDJ、BIAS、ATR、
  20日波动率、量比、换手率、相对基准(沪深300/恒指/标普500 自动选)超额收益、近120日支撑压力位。K线与技术指标
  **统一前复权**，元数据声明；数据源不支持复权则显式标"未复权"警告。
- **归一化**：TTM 统一并标注覆盖报告期区间；货币随绝对值标注（默认不换算）。准则差异(CAS/IFRS/US GAAP)背景
  见 `references/cross-market-notes.md`，第一版不做自动调整但 schema 预留 `accounting_standard`。

## 🧬 医药 / 生物科技模块

命中医药标签时叠加，代码在 `scripts/pharma_valuation.py`，**方法学与基准数值在
`references/pharma-valuation.md`（带目录，必读）**。核心是自动区分估值范式并做 sum-of-the-parts：
商业化阶段用通用估值 + 专利悬崖调整；临床阶段 biotech 用 rNPV；大型 pharma = 已上市组合常规估值 + 在研管线
rNPV 相加，分部展示。

rNPV 六步、PoS 阶段基准、**折现率不双重计罚规则**、LOE 小分子 vs 生物药衰减、敏感性、催化剂——全部细节见
`references/pharma-valuation.md`。这里只强调三条最易错的红线，脚本已实现，你审阅输出时据此核对：

1. **折现率不双重计罚**：rNPV 用独立的 `clinical_discount_rate`（分层默认：大型pharma 10% / 临床biotech
   12.5% / pre-revenue早期 15%），**绝不复用通用 WACC**。因为临床风险已由 PoS 单独扣除，再套高 WACC 等于罚
   两次，会把有价值的管线算成一文不值。
2. **累积 PoS = 从当前阶段到批准各阶段成功率连乘**，当前阶段从 ClinicalTrials.gov 实读，不靠猜。
3. **假设全暴露**：所有 `user_assumption` 必须进报告末尾的 **🔴 需人工核对清单**，逐项列取值、来源类型、
   及其对 rNPV 的影响方向。这是医药估值诚实性的底线——绝不把主观假设伪装成客观结论。

## 呈现层（四种输出，数字必须完全一致）

`{symbol}_analysis.json` 是**唯一数据源**（single source of truth），其余三种全部从它渲染。格式规范见
`references/output-templates.md`。

1. **JSON**：全部计算 + 三件套 + quality_report + data_gaps +（医药）`pharma` 段。schema 见
   `references/analysis_schema.json`，稳定，因为未来建议层只消费它。
2. **Markdown 看板**（你写）：核心指标表 + 门禁状态 + 3–5 解读要点（+ 医药 rNPV 小结 + 核对清单）。
3. **HTML dashboard**（`render_html.py`）：单文件离线可开，ECharts(CDN)。K线+均线、MACD/RSI、估值卡、
   杜邦瀑布、比率趋势、逆向验证红黄绿灯、门禁面板；医药额外 rNPV 瀑布/敏感性 tornado/管线矩阵/LOE 时间线/
   催化剂日历。中文界面，深浅色自适应。**必须把完整 JSON 以 `<script id="analysis-data"
   type="application/json">` 内嵌**，供机检对账。
4. **Excel 底稿**（`render_xlsx.py`）：Raw Data sheet 存原始数据，Ratios sheet 用**活公式**引用 Raw Data
   （非硬编码）；医药额外 rNPV sheet，PoS/峰值销售/折现率为可编辑输入格，rNPV 用公式实时重算。

## 质量门禁要点

`quality_gate.py` 在渲染前跑。Critical（阻断，只出降级报告）：核心字段缺失；跨源收盘价差 >1%；TTM 财报距今
>12 个月；医药 rNPV 误用通用 WACC；临床前/早期资产累积 PoS >30%。Warning（标注不阻断）：字段覆盖 <80%；
PE/PB 异常跳变；技术指标基于未复权；`user_assumption` 未进核对清单；判为临床 biotech 但管线为空。完整清单
见 `references/metrics-formulas.md` 末节。

## 边界与免责

自用研究工具，所有输出仅为数据分析，**不构成投资建议**；每份报告末尾附一行免责声明。医药 rNPV 尤其要强调
是基于大量主观假设的情景估算，**非目标价**。不接入任何交易接口，不做下单功能。

## 参考文件索引（按需加载，渐进式）

- `references/data-sources.md` — 降级链、各接口字段映射、行业标签判定、已知坑
- `references/metrics-formulas.md` — 每个通用指标的精确公式与口径 + 门禁完整清单
- `references/pharma-valuation.md` — rNPV 方法学 + PoS 基准表 + LOE + 折现率规则（**医药必读，带目录**）
- `references/cross-market-notes.md` — 三市场准则/节奏/复权差异
- `references/output-templates.md` — 四种输出的格式规范与 Markdown 模板
- `references/debug-playbook.md` — 常见故障→诊断→修复手册（构建/运维期查表）
- `references/analysis_schema.json` — 输出 JSON 的 schema（交付物，机检依据）

构建/自测脚手架在 `scripts/selfcheck/`（回归入口 `run_regression.py`）。这些是维护用，正式分析走上面的工作流。
