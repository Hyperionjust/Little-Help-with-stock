# 通用指标精确公式与口径（compute_metrics.py 的规范）

> 脚本按此实现，golden.json 按此从 fixture 原始报表手算。任何口径歧义在此裁定。
> 记号：营收=Revenue, 净利=NetIncome(归母), 毛利=GrossProfit, 权益=Equity(归母), 资产=Assets,
> OCF=经营现金流。TTM=最近四个季度滚动（无季度则用最近年报，口径在字段 period 标注）。

## 估值 Valuation

| 指标 | 公式 | 口径说明 |
|---|---|---|
| 市值 market_cap | `price × total_shares` | 优先取源提供市值；缺则 price×股本 |
| PE-TTM（**默认**） | `market_cap / NetIncome_TTM` | 归母净利 TTM |
| PE 静态 | `market_cap / NetIncome_LYR` | 上一完整财年归母净利 |
| PE 预测 | `market_cap / NetIncome_FY_est` | 有一致预期时；无则 null 入 data_gaps |
| PB | `market_cap / Equity` | 归母净资产（最新期） |
| PS-TTM | `market_cap / Revenue_TTM` | |
| PEG | `PE_TTM / (net_income_cagr_3y × 100)` | 增速用百分数的数值；CAGR<=0 则 null |
| EV/EBITDA | `(market_cap + total_debt − cash) / EBITDA_TTM` | EBITDA=营业利润+折旧摊销；缺折旧则用营业利润近似并标注 |
| 股息率 | `dividend_per_share / price` 或 `total_dividend / market_cap` | |

## 盈利质量 Profitability

| 指标 | 公式 |
|---|---|
| ROE | `NetIncome_TTM / Equity_avg`；Equity_avg=(期初+期末)/2，无期初则用期末并标注 |
| ROA | `NetIncome_TTM / Assets_avg` |
| ROIC | `NOPAT / InvestedCapital`；NOPAT=营业利润×(1−有效税率)，InvestedCapital=有息负债+权益 |
| 毛利率 | `GrossProfit_TTM / Revenue_TTM` = `(Revenue−COGS)/Revenue` |
| 净利率 | `NetIncome_TTM / Revenue_TTM` |
| 现金含量 | `OCF_TTM / NetIncome_TTM` |

**杜邦三分解**（`dupont`）：
- 净利率 = `NetIncome / Revenue`
- 资产周转 = `Revenue / Assets_avg`
- 权益乘数 = `Assets_avg / Equity_avg`
- **乘积校验** `product_check = 净利率 × 资产周转 × 权益乘数` 应 ≈ ROE（性质断言，容差 ±1%）。
  推导：`(NI/Rev)×(Rev/A)×(A/E) = NI/E = ROE`。

## 逆向验证 Reverse validation（差异化重点）

- 应收增速 vs 营收增速：`ar_growth − revenue_growth`（YoY）。显著为正 → 应收扩张快于收入，回款质量存疑。
- 存货增速 vs 营收增速：`inventory_growth − revenue_growth`（YoY）。显著为正 → 积压风险。
- OCF 与净利 3 年背离度：`ocf_ni_divergence_3y = mean( (OCF_t − NI_t) / |NI_t| )`（近 3 年）。持续大幅
  为负 → 利润未转化为现金。
- `flags`：上述任一超阈值（默认 15 个百分点 / 背离度 <−0.2）时生成红黄绿灯项。

## 偿债与营运 Solvency

| 指标 | 公式 |
|---|---|
| 资产负债率 | `TotalLiabilities / TotalAssets` |
| 流动比率 | `CurrentAssets / CurrentLiabilities` |
| 速动比率 | `(CurrentAssets − Inventory) / CurrentLiabilities` |
| 利息保障倍数 | `EBIT / InterestExpense`；利息≈0 则 null 标注 |
| 应收周转天数 | `365 × AR_avg / Revenue_TTM` |
| 存货周转天数 | `365 × Inventory_avg / COGS_TTM` |

## 成长 Growth

| 指标 | 公式 |
|---|---|
| 营收 YoY | `Revenue_TTM / Revenue_TTM_prevYear − 1` 或年报 `Rev_t/Rev_{t-1}−1` |
| 净利 YoY | 同上用 NetIncome |
| 营收 QoQ | `Rev_q / Rev_{q-1} − 1`（有季度数据时） |
| 3年 CAGR | `(Rev_t / Rev_{t-3})^(1/3) − 1`；需 4 个年度点 |
| 5年 CAGR | `(Rev_t / Rev_{t-5})^(1/5) − 1`；需 6 个年度点 |

## 技术面 Technicals（统一前复权 qfq）

- MAn = 最近 n 日收盘均值（简单均线）。多空排列：MA5>MA10>MA20>MA60 → 多头；反之空头；否则纠缠。
- MACD：`EMA12`、`EMA26`（EMA 递推，首值用 SMA 种子）；`DIF=EMA12−EMA26`；`DEA=EMA9(DIF)`；
  `MACD柱=2×(DIF−DEA)`（A股习惯的 2 倍口径，元数据标注）。**定义关系**（性质断言）：
  `柱/2 ≈ DIF−DEA`，`DEA = EMA9(DIF)`。
- RSI(n) = `100 − 100/(1+RS)`，`RS = 平均涨幅_n / 平均跌幅_n`（Wilder 平滑）。取 6/12/24。
- KDJ：`RSV=(C−Ln)/(Hn−Ln)×100`（n=9）；`K=EMA(RSV,3)` 递推；`D=EMA(K,3)`；`J=3K−2D`。
- BIAS(n) = `(C − MAn)/MAn × 100`。
- ATR(14) = Wilder 平滑的真实波幅；`TR=max(H−L, |H−Cprev|, |L−Cprev|)`。
- 20日波动率 = `std(日对数收益, 20) × sqrt(252)`（年化，元数据标注）。
- 量比 = `今日成交量 / 过去5日均量`。换手率 = `成交量 / 流通股本`。
- 相对强弱：`个股区间收益 − 基准区间收益`，基准按市场自动选（A→沪深300, HK→恒指, US→标普500），
  取 20/60/120 日。
- 支撑压力：近 120 日的分位数法——支撑=近120日最低价的滚动摆动低点聚类 / 20分位；压力=80分位（方法在
  字段 formula 标注）。

## TTM 与复权口径

- 每个 TTM 指标的 `period` 字段必须标覆盖的报告期区间（如 `TTM 2023Q4–2024Q3`）。
- K线与技术指标统一前复权，`meta.adjust_mode="qfq"`。源不支持复权 → `meta.adjust_mode="none"` 并触发
  technical 未复权 warning。

## 质量门禁完整清单（quality_gate.py）

**Critical（不通过 → `quality_report.degraded=true`，只出降级报告）：**
1. `missing_core_field`：现价 / 市值 / PE 等核心字段缺失。
2. `cross_source_price_mismatch`：两 provider 都返回收盘价且相对差 >1% → 标红并阻断。
3. `stale_financials`：TTM 财务数据报告期距今 > 12 个月。
4. `pharma_double_penalty`（医药）：rNPV 用了通用 WACC 而非独立 clinical_discount_rate。
5. `pharma_pos_implausible`（医药）：临床前/早期资产累积 PoS > 30%。

**Warning（输出但显式标注）：**
1. `low_field_coverage`：字段覆盖率 < 80%。
2. `pe_pb_outlier`：PE/PB 异常跳变（如 PE 上期 20 → 本期 200，提示未处理非经常损益或除权）。
3. `unadjusted_technicals`：技术指标基于未复权数据。
4. `uncovered_user_assumption`（医药）：任何 user_assumption 未进需人工核对清单。
5. `empty_pipeline`（医药）：判为临床 biotech 但管线数据为空（获取可能失败）。

门禁结果写入 `quality_report`，在所有呈现形态可见。
