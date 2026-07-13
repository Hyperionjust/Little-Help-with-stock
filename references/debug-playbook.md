# debug-playbook — 常见故障 → 诊断 → 修复

构建/运维期查表。失败先分类再修，四类处置协议完全不同（对应 spec §10.5）。

## 故障分类与处置协议

### 网络类（超时/限流/证书/ProxyError）
- **离线 fixtures 阶段出现网络错误** = 测试隔离被污染。修隔离（确保跑 fixture 而非真联网），**不是加重试**。
- **P4 联网阶段出现** = 检查 `with_retry` 的 3 次指数退避与降级链是否按设计触发；确认失败字段进了 `data_gaps`。
- 已知：eastmoney/新浪/yahoo 在受限网络被拦是环境问题，不改代码，走降级链。

### 数据漂移类（KeyError / 字段为空 / 类型不符）
- 把实际响应原样 dump 到 `debug/` 目录，肉眼比对 `references/data-sources.md` 的字段映射。
- **只在 provider 层修映射**——严禁在计算层加 try-except 吞错，那会把数据问题伪装成计算正确。
- baostock 返回全字符串 → 记得转 float；yfinance 单位是元 → ÷1e6。

### 逻辑类（golden 或性质断言失败）
- 打印逐步中间值（如 dupont 三因子、TTM 分子分母），先分清是**取数错**还是**公式错**，再动手。
- 性质断言（杜邦乘积≈ROE、PE×净利≈市值、MA20=窗口均值、柱/2=DIF−DEA）能定位到底是哪一环。
- golden 本身可能录错 → 用性质断言交叉验证；改 golden 必须在 BUILD_LOG 记 `[断言变更]`。

### 渲染类（一致性/解读检查失败）
- **先确认 JSON 正确**（JSON 是唯一数据源），再查渲染；**严禁反过来改 JSON 迁就渲染**。
- HTML 不一致：查是否忘了内嵌 `<script id="analysis-data">`，或展示时又算了一遍数字（应直接取 JSON 值）。
- Excel 被判硬编码：Ratios 的 B 列必须是 `=` 公式且含 `'Raw Data'` 引用。
- interpret_check 报幻觉：解读里写了 JSON 中不存在的数字（如目标价/涨幅）——删掉，LLM 只解读不算术。

## 已沉淀案例（每修一个 bug 追加）

### CASE-001：jsonpath 依赖在系统 pip 下编译失败（install_layout AttributeError）
- 症状：`pip install akshare` 因 jsonpath 旧 setup.py 在新 setuptools 下 `AttributeError: install_layout`。
- 归因：系统 Python 的 setuptools 与旧包不兼容。
- 修法：改用独立 venv（`python -m venv`）+ 升级 pip/setuptools/wheel 后再装。构建环境专用，不影响 skill 运行。

### CASE-002：构建容器 egress 防火墙（仅 api.fda.gov 可 Python 直连）
- 症状：clinicaltrials/eastmoney/yahoo/baostock 全部 ProxyError；openFDA 可直连。
- 归因：沙箱 egress allowlist。**非代码 bug**。
- 修法：离线 fixtures 用真实数值重建（provenance 标注）；P4 联网冒烟标注为「在用户环境运行」；降级链用猴子
  补丁离线验证（test_providers 的 `_RaisingProvider`）。

### CASE-004：内嵌图表序列污染防幻觉值池，弱化 interpret_check 守卫
- 症状：给 analysis JSON 加了 `technicals.series`（K线/MACD/RSI 数组）后，`test_interpret` 的
  "捏造数字应被抓" 用例失败——伪造的目标价/涨幅巧合命中某个 RSI 刻度（2% 容差内）被误判"存在"。
- 归因：`collect_values` 把几百个图表数组数字纳入了"可引用指标值"池，稀释了守卫。
- 修法：`interpret_check._SKIP_KEYS` 排除 series/stage_breakdown/sensitivity/candle/kline 等图表明细键，
  解读只允许引用 headline 指标值。这是**加强**守卫（渲染类问题先确认 JSON 正确、不迁就渲染的反向应用：
  这里是校验器自身范围错，收窄范围而非放宽断言）。回归只增不减。

### CASE-003：港股/亏损 biotech 字段覆盖率 <80% 触发 warning
- 症状：BeiGene 覆盖率 77% 报 low_field_coverage。
- 归因：亏损公司 PEG/股息率/部分比率天然为 null。**正确行为，非 bug**——门禁如实反映数据局限。
- 处置：不修，warning 属预期；解读时说明"因亏损，PEG/股息率不适用"。
