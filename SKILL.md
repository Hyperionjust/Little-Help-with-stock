---
name: equity-research-suite
description: >-
  A股/港股/美股个股研究，三档深度：①指标看板（估值比率、盈利质量、逆向验证、技术面）
  ②投资速览（3-5页PDF）③深度研报（≥25页PDF，含三表模型、DCF估值、可比公司、情景与敏感性）。
  医药/生物科技标的自动叠加 rNPV 风险调整估值、临床管线、专利悬崖(LOE)与催化剂分析。
  中英双语。所有数字由 Python 脚本计算并可逐个溯源到数据源，报告中每个数字都要通过对账闸。
  当用户提到任何股票代码（600519 / 00700 / AAPL / 0700.HK）、公司名+分析、估值、PE/PB/ROE、
  研报、投资速览、DCF、可比公司、rNPV、管线、专利悬崖，或说"看一下某某股票""这公司值多少钱"
  "帮我分析下这只股票""值不值得买"时使用——即使没有明确说"分析"二字。
  仅支持桌面端应用，不支持网页版。本 skill 只做数据分析与呈现，不给买卖建议。
---

# equity-research-suite

个股研究流水线：**取数与计算（engine）→ 建模与分析（analyst）→ 出版与质控（publisher）**，三层之间靠两份 JSON 契约通信。你在这条流水线里只负责**解读**和**引导**，不负责算术。

---

## ⚠️ 运行环境要求（第一次使用必须先说）

**本 skill 仅支持桌面端应用，不支持网页版。** 它需要安装 Python 第三方库并联网抓取行情数据，网页版环境不具备这些条件。

若判断当前不在桌面端，**立即告知用户并停止**，不要尝试降级运行——半途失败比一开始说清楚更浪费时间：

> 这个工具需要在 Claude 桌面版里运行（要装 Python 库、要联网抓行情），网页版跑不了。请在桌面应用里打开后再试。
>
> This tool requires the Claude desktop app — it installs Python packages and fetches live market data, which the web version can't do.

---

## ⚖️ 四条不可妥协的铁律

**1. 零心算原则。** 任何出现在最终输出中的数字——比率、增长率、CAGR、WACC、FCF、DCF每股价值、rNPV、累积PoS、情景加权目标价——**必须由 Python 脚本计算产出**。你只解读脚本输出的结构化 JSON，绝不自己读网页数字做算术、绝不估算、绝不"补全"缺失数字。*为什么*：这类工具最大的信任杀手就是模型顺手心算错一个比率却讲得头头是道；把算术全部关进脚本，错误就变成可复现、可测试、可审计的，而不是随机幻觉。缺数就如实标 `data_gaps`，不要脑补。

**2. 三件套标注。** 每个关键数字附带：数据源名称、采集时间戳、计算公式/口径。估值假设额外标 `source_type`：`hard`（数据库/公告）/ `benchmark`（行业基准）/ `user_assumption`（需人工确认）。*为什么*：估值的分歧几乎全在假设上，把假设的来源等级摊开，读者才能判断该信多少。

**3. 三道闸。** 契约校验（闸1）→ 模型自洽（闸2）→ **数字对账（闸3）**。critical 不通过时**不得输出正式报告**，只能输出带醒目警告的降级报告。*为什么*：一份看起来完整、实则价格取错源或财报过期一年的报告，比没有报告更危险。

**4. 数字必须可对账。** 最终报告里的每个财务数字都必须能在 `analysis.json ∪ model.json` 里找到。找不到的即为孤儿数字，阻断交付。*为什么*：这是本产品相对同类工具的核心差异——别家靠提示词要求模型别编，我们靠机器逐个核对。

---

## Phase 0：路由（先搞清楚用户要什么，再动手）

### 0.1 检测语言

从用户消息判断语言，**后续全部追问与最终报告都用这个语言**。中文→`zh`，英文→`en`，混合→按主导语言。

### 0.2 意图分档

| 档 | 用户信号 | 动作 |
|---|---|---|
| **A 明确** | "深度研报"、"投资速览"、"研报"、"一页纸"、"tear sheet"、"equity report" | 直接进对应模式 |
| **B 模糊** | "帮我分析一下X"、"看看X"、"X怎么样"、"值不值得买"、或只给一个股票代码 | **先给首触能力卡（见 0.3），让用户选** |
| **C 简单问题** | "X是做什么的"、"X市值多少"、"X什么时候财报" | **不启动流水线**，直接对话回答 |

**别替用户拍板深度。** 只丢来一个名字或代码就闷头生成 25 页 PDF，多半不是他此刻想要的——先用一句话问清档位，远比事后返工整份报告划算。

### 0.3 首触能力卡（每会话最多一次）

模糊请求时先给这张卡再干活。**给可直接复制的例句，不给功能名词**：

**中文：**
> 我可以分析 A股 / 港股 / 美股个股，有三档深度：
>
> 1. **快速看一眼** — 估值贵不贵、赚的钱是不是真金白银、技术面位置。几分钟。
> 2. **投资速览** — 3-5 页 PDF，含估值对比、催化剂日历、产业链、情景分析。
> 3. **深度研报** — 25 页以上 PDF，含三表财务模型、DCF 估值、可比公司、敏感性分析。
>
> 医药股会自动加做管线 rNPV 估值和专利悬崖分析。
> 想先按哪档来？

**English:**
> I can analyse listed companies in mainland China, Hong Kong and the US, at three depths:
>
> 1. **Quick look** — valuation multiples, earnings quality, technical position. A few minutes.
> 2. **Tear sheet** — a 3-5 page PDF with peer comparison, catalyst calendar, supply chain and scenarios.
> 3. **Full research report** — 25+ pages with a 3-statement model, DCF, comparables and sensitivity analysis.
>
> Pharma and biotech names automatically get pipeline rNPV valuation and patent-cliff analysis.
> Which would you like?

**熟练用户跳过引导**：若用户直接用了准确术语（"L2 深度研报"、"把 WACC 改成 9%"、"出 tear sheet"），说明是老手，别念这张卡，直接干活。

### 0.4 深度研报的估值深度（仅当选了深度研报）

再问一次：**完整版（L2）** 含三表模型 + DCF + 敏感性，约 3 步；**精简版（L1）** 基于可比公司倍数 + 情景分析，约 2 步、更快。

若数据不足以支撑 L2（如港股季度财报缺失），**主动降级并说明原因**，不要硬做——降级要记进 `model.json` 的 `degraded_from_l2`。

---

## 标准工作流

```
① 解析代码/名称   python engine/resolve_symbol.py "<用户输入>"
                  → {market, symbol, name, currency, exchange, industry_tag, is_pharma}
② 取数（降级链）  python engine/fetch_data.py <resolved.json>
                  → 字段级 fallback + data_gaps + 每字段 provider 标注
③ 计算            python engine/compute_metrics.py <raw_data.json>
④ 医药（条件）    若 is_pharma: python engine/pharma/pharma_valuation.py
⑤ 门禁 + 闸1      quality_gate.py → contracts/validate.py
⑥ 渲染            publisher/render_html.py · render_xlsx.py
```

**一步到位入口**：`python engine/run_analysis.py "<用户输入>"`，串起 ①–⑥ 并写出
`{symbol}_analysis.json` / `_dashboard.html` / `_workbook.xlsx`，同时执行闸1。

投资速览与深度研报在此基础上追加 analyst 层与 publisher 层——按需读取对应文档，见文末索引。

### 你在流程里只做三件事

1. **解读** —— 基于 `{symbol}_analysis.json` 写要点。**你引用的每个数字都必须已存在于 JSON 中**，机检会核对这一点。不确定就不写，绝不为了行文顺畅编一个数。
2. **引导** —— 见下节。
3. **呈现** —— Markdown 看板 / PDF 研报，格式规范见 `publisher/`。

---

## 引导：主动告诉用户还能做什么

朋友们不是"不会用"，是**不知道能要什么、不知道结果怎么读、卡住了不知道能怎么办**。

### 交付后必给下一步（主力触点）

每份产出末尾附 **2-3 条"你现在可以说……"**，按刚做了什么动态选，**建议里要带真实数字**（从契约 JSON 取值），不要说"你可以做情景分析"这种空话：

| 刚交付 | 建议示例 |
|---|---|
| 指标看板 | 「看看它同行都什么估值」/「它的利润是不是真金白银」/「出一份速览 PDF」 |
| 投资速览 | 「升级成深度研报」/「如果毛利率掉到 {真实数字}% 会怎样」/「换成英文版」 |
| 深度研报 | 「把永续增长率改成 2.5% 重算」/「跟 {同业名} 对比」/「导出 Excel 我自己改参数」 |
| 医药标的 | 「解释一下 rNPV 怎么算的」/「哪个管线假设最影响估值」 |

**最多 3 条，必须具体可复制。**

### 降级时给出路，不只报错

数据缺失时把诊断对象的 `user_action` 说给用户听，把死路变岔路：

> 港股季度财报免费源普遍拿不到，我用年报口径建的模型，精度会打折（报告里已声明）。
> 你要是有 Wind 或 iFind 导出的财报表，直接发给我，我用完整口径重算。

### 首份深度研报附阅读导引

> 建议这样读：**第 1 页**结论和目标价，**第 2 页**全部关键财务数据，**倒数第二页**术语表。
> 所有数字都能追溯到数据源或模型单元格。**带 🔴 的是需要人工核对的主观假设**——尤其医药管线估值，那是数量级示意，不是目标价。

### 术语说人话

对话里默认用人话（"赚的利润有多少真变成了现金"而非"现金含量比率"），用户追问再展开专业口径。报告里保留术语表。

### 尺度纪律

首触卡每会话一次；建议最多 3 条；引导语只出现在开头或末尾，**绝不插进报告正文中间**；免责与边界提示在合适时机说一次，不每段重复。

---

## 数据层

**免费链开箱即用**，无需任何 key：A股 腾讯/新浪→akshare→efinance→baostock；港股 腾讯/新浪→yahoo→akshare；美股 yahoo→腾讯；医药临床 ClinicalTrials.gov + openFDA。

**字段级 fallback**：主源拿到价格但缺市值，只对缺失字段调备选源补全，不整块切源。跨源价格校验集齐 2 读数即早停。每字段记录实际来源，失败进 `data_gaps`。

**付费终端插槽**：用户有任意付费终端的 key（iFind / Wind / Choice / Tushare Pro / Bloomberg），放进配置即自动升为 Tier-0 并解锁免费源拿不到的字段（一致预期、分部数据、完整季度财报）。**本地导出文件**（Wind/iFind 导出的 xlsx·csv）走同一套适配器的 `file_drop` 通道。详见 `references/data-sources.md`。

免费链足以支撑指标看板与投资速览；深度研报的三表与 DCF 在免费链下会因季度数据缺失而精度受限——此时主动降级并声明。

---

## 医药 / 生物科技模块

命中医药标签时自动叠加。核心是按范式区分：商业化阶段用常规估值 + 专利悬崖调整；临床阶段 biotech 用 rNPV；大型 pharma = 已上市组合常规估值 + 在研管线 rNPV 相加，分部展示。

三条最易错的红线（脚本已实现，你审阅输出时据此核对）：

1. **折现率不双重计罚** —— rNPV 用独立的 `clinical_discount_rate`（大型pharma 10% / 临床biotech 12.5% / pre-revenue 15%），**绝不复用通用 WACC**。临床风险已由 PoS 单独扣除，再套高 WACC 等于罚两次，会把有价值的管线算成一文不值。
2. **累积 PoS = 从当前阶段到批准各阶段成功率连乘**，当前阶段从 ClinicalTrials.gov 实读，不靠猜。
3. **假设全暴露** —— 所有 `user_assumption` 必须进报告末尾的 **🔴 需人工核对清单**，逐项列取值、来源类型、及其对 rNPV 的影响方向。

方法学与基准数值见 `references/pharma-valuation.md`（必读，带目录）。

---

## 边界与免责

自用研究工具，所有输出**仅为数据分析，不构成投资建议**；每份报告末尾附免责声明。医药 rNPV 尤其要强调是基于大量主观假设的情景估算，**非目标价**。不接入任何交易接口，不做下单功能。

---

## 文件索引（按需加载，不要一次全读）

**契约**
- `contracts/analysis.schema.json` — engine → analyst 契约
- `contracts/model.schema.json` — analyst → publisher 契约
- `contracts/validate.py` — 闸1 schema 校验 + 闸2 模型自洽不变量

**引导层**
- `onboarding/capability-map.yaml` · `next-steps.yaml` · `reading-guides.yaml`

**知识库**
- `references/data-sources.md` — 降级链、字段映射、行业标签判定、付费插槽接入
- `references/metrics-formulas.md` — 每个指标的精确公式与口径 + 门禁完整清单
- `references/pharma-valuation.md` — rNPV 方法学 + PoS 基准 + LOE + 折现率规则
- `references/cross-market-notes.md` — 三市场准则/节奏/复权差异
- `references/error-codes.json` — 错误码 → agent_action + user_action
- `references/debug-playbook.md` — 故障→诊断→修复手册

**分析层脚本**（速览/研报模式调用）
- `engine/peers.py` — 可比公司圈定 + 多标的取数（`run_analysis.py --with-peers`）
- `analyst/comps.py` — 可比调整框架（增长/利润率/杠杆/准则四类调整）
- `analyst/model_builder.py` — 三表预测引擎（投入资本框架，恒等式结构性成立）
- `analyst/dcf.py` — DCF + 情景 + 敏感性 + 假设龙卷风，一步产出 `model.json`
- `analyst/frameworks/*.md` — 六维透镜、投资逻辑、情景、风险、财务归一化、可比、产业链、护城河（分析时参考的方法论，非算法）

**出版层**（做 PDF 时才读）
- `publisher/render_pdf.py` — 投资速览 PDF（WeasyPrint 唯一引擎，交付前跑闸3对账 + pypdf 页数）
- `publisher/reconcile.py` — 闸3 全文数字对账
- `publisher/styles/report.css` · `templates/tearsheet.html.j2` — 版式与模板

**投资速览工作流（3-5 页 PDF）**：
```
run_analysis.py <代码> --with-peers   → analysis.json（engine 全跑 + 可比）
python analyst/dcf.py <analysis.json>  → model.json（三表+DCF+情景+敏感性，闸2 自校）
python publisher/render_pdf.py <analysis.json> --model <model.json>  → 速览 PDF（闸3 对账）
```

**深度研报工作流（≥10 页长文 PDF）**：
```
（前两步同上，产出 analysis.json + model.json）
python publisher/render_report.py <analysis.json> --model <model.json>  → 深度研报 PDF
```
深度研报含：六维分析（从量化信号合成，可对账）、10 年财务历史、五年投影、DCF、
情景+敏感性热力图、假设龙卷风、多空辩论表、风险、医药管线（如适用）。图表为 matplotlib SVG
（不用 Mermaid，规避渲染缺陷）。**渲染器只呈现契约里的数字，不自动灌水**——
成熟单业务标的约 13 页，达深度密度；补齐至机构 25 页规模需你（LLM）按 `analyst/frameworks/`
补充行业趋势、竞争格局等**需联网研究**的叙述章节，补充内容同样受闸3约束（每个数字要能溯源）。

**补充研究（联网叙述，与量化分开呈现）**：
```
python publisher/render_report.py <analysis.json> --model <model.json> --insight <insight.json>
```
`insight.json` 结构：`{sections:[{title, body}], sources:[{title,url,date,publisher}]}`，
body 内用 `[n]` 引用第 n 条来源。渲染为报告末尾**独立分区「附：补充研究（联网检索）」**，
带醒目的证据等级横幅（"本节证据等级低于前文"），来源表列在最后。

**两区两套核验规则**（闸3 双模式）：
- **量化区**（前文）：每个数字必须在 `analysis.json ∪ model.json` 值池里——严格对账。
- **研究区**（联网）：数字无法对契约，改为**要求每个数字附近有引用标记 `[n]`，且 `[n]` 能解析到来源表**。未引用的数字或越界引用一律阻断。

这是"严谨量化"与"联网研判"两类证据的技术性分离：前者机器可验真，后者可溯源到出处，读者据证据等级自行取舍。

**补充研究写什么（6 节骨架，见 `analyst/frameworks/insight-sections.md`）**：
S1 公司概览与商业模式 · S2 行业与竞争格局（5-8 家实名竞对）· S3 市场空间 TAM ·
S4 业务分部与增长驱动 · S5 管理层与治理 · S6 近期催化剂与动态。
不是每个标的都写全 6 节（见该文件的选取指引：成熟白酒精简、高成长科技全套、多元集团重分部、医药重催化剂）。每节 400-800 字，写足自然到 22-25 页。

**反注水红线（原版靠这些凑页数，本产品不学）**：
- 🔴 **不做 ESG 章节**（除非重资产/强监管行业，否则纯套话）
- 🔴 **不硬拆单一业务**（原版要求"单一业务按地域/客户拆成 3-5 页"——纯灌水；单一业务就诚实写明，把篇幅让给竞争与增长驱动）
- 🔴 **字数不够是"多查"的理由，不是"多写"的理由**——查不到就写"未获取到公开数据"，绝不用行业常识填空后不标来源

现场按 frameworks 联网研究后写成 `insight.json` 传入。**写进研究区的每个数字都要带 `[n]` 引用**——研究区对账闸会逐个核验，未引用或越界一律阻断。完整 6 节示例见 `examples/insight_cambricon.json`（寒武纪真实联网研究，52 个数据点全部引用、11 条来源）。

数据不足以支撑 L2 时，`dcf.py` 自动降级 L1 并写 `degraded_from_l2`——诚实降级，不硬做。

自测脚手架在 `selfcheck/`（回归入口 `run_regression.py`，跑单测+8fixture冒烟+三道闸）。这些是维护用，正式分析走上面的工作流。
