# 数据源：降级链、字段映射、行业标签判定、已知坑

## 降级链（fetch_data.build_chain）

| 市场 | 顺序 |
|---|---|
| A股 | tushare(有 `TUSHARE_TOKEN`) → akshare → efinance → baostock |
| 港股 | akshare → efinance → yfinance |
| 美股 | yfinance → akshare(美股接口) |
| 医药临床 | ClinicalTrials.gov v2 + openFDA（**叠加**，非降级） |

**字段级 fallback**（`merge_fields`）：对每个标准化字段独立取「第一个非 None」的 provider 值，逐字段记录来源。
主源拿到 price 但缺 market_cap 时，只对 market_cap 调备选源，**不整块切源**。

## 标准化字段（计算层依赖，见 providers/base.py）

- 行情：`price, market_cap, prev_close, float_shares, total_shares`
- 财务（annual 列表，最近财年 index 0）：`revenue, cogs, gross_profit, operating_income,
  net_income, ocf, total_assets, total_liabilities, equity, current_assets, current_liabilities,
  inventory, accounts_receivable, cash, total_debt, interest_expense, effective_tax_rate,
  eps_diluted, shares_diluted, depreciation`
- K线：`adjust, source, as_of, dates[], open[], high[], low[], close[], volume[]`

## 各接口字段映射（供 provider 层修映射时对照）

**akshare A股行情** `stock_zh_a_spot_em`：最新价→price、总市值→market_cap、昨收→prev_close、流通股→float_shares。
**akshare A股K线** `stock_zh_a_hist(adjust='qfq')`：日期/开盘/最高/最低/收盘/成交量。
**akshare 港股** `stock_hk_spot_em` / `stock_hk_hist`。
**yfinance** `Ticker.fast_info`（last_price/previous_close/market_cap/shares）、`.history(auto_adjust=True)`≈前复权、
`.financials/.balance_sheet/.cashflow`（单位为元，脚本 ÷1e6 转百万）。港股代码转 `NNNN.HK`。
**baostock** `query_history_k_data_plus(adjustflag='2'=前复权)`，只有 K线/价格，**无市值接口**。
**efinance** `get_quote_history(fqt=1)` 前复权；`get_base_info` 行情快照。
**tushare（付费/积分）** `daily_basic`（close/total_mv/total_share/float_share，total_mv 单位万元 ×1e4）。
**ClinicalTrials.gov v2** `GET /api/v2/studies?query.spons=<sponsor>&fields=...`：
  `protocolSection.identificationModule.nctId` / `.statusModule.overallStatus` /
  `.designModule.phases` / `.conditionsModule.conditions` / `.armsInterventionsModule.interventions[].name` /
  `.statusModule.primaryCompletionDateStruct.date`；`totalCount` 为全量计数。
**openFDA** `GET /api/fda/drug/drugsfda.json?search=openfda.brand_name:"X"`：products[].marketing_status 等。

## 行业标签判定（resolve_symbol）

命中即 `is_pharma=True`，激活医药模块。三路判定取并集：
1. 内置医药代码名录 `PHARMA_SYMBOLS`（600276/603259/300760/BGNE/...，可扩展）。
2. 名称关键词：医药/生物/制药/疫苗/药业/医疗/pharma/bio/therapeutics/biotech/...。
3. 行业字符串关键词（provider 补全行业字段后）：申万医药生物 / GICS Health Care / 港股医疗保健 / pharmaceutical。

## 付费源接入（预留）

新增 Tushare Pro 高级接口 / iFind / Wind MCP / 医药付费源（Cortellis/Evaluate/DrugPatentWatch）时，
**只需新增一个 provider 文件**实现 `base.Provider` 的方法子集，并把它插入 `build_chain` 对应市场链的前部
（Tier-0）。计算层不改一行——这是抽象基类的全部价值。付费源的专利到期精确日期可替换 LOE 模块里标
`user_assumption` 的估计值。

## 交叉验证（可选）

若环境存在用户 skill `drug-intel` / `rare-disease-epi`，可在解读时交叉参考其数据源清单（药物身份、
流行病学口径）。但本 skill 的临床数据**以自带 clinicaltrials/openfda provider 为准**，保证自洽。

## 已知坑

- **eastmoney/新浪/腾讯行情接口**在部分受限网络（如 CI/沙箱）会被防火墙或 robots 拦截 → 联网阶段失败属环境
  问题，走降级链 + data_gaps，不是代码 bug（见 debug-playbook「网络类」）。
- **yfinance 财报单位是元**，务必 ÷1e6 转百万，否则市值/营收量级错。
- **港股代码补零**：0700 → yfinance 用 `0700.HK`，akshare 用 `00700`。
- **A股应收账款**对白酒等预收款行业极小甚至可忽略，逆向验证的应收增速对这类标的意义弱，解读需说明。
- **baostock 需先 login()**，用完 logout()；返回全是字符串，务必转 float。
- **ClinicalTrials v2 分页**用 `nextPageToken`，一次 pageSize 上限 1000，默认脚本翻 3 页够用。
