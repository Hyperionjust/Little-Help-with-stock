# 第三方归属声明

本产品在若干前作基础上构建。此处如实列明来源、许可证状态与本产品的处理方式。

---

## 1. stock-metrics-pro（本产品的 engine 层基础）

- **来源**：本项目作者自研
- **许可证**：MIT
- **处理**：整体继承并改造。engine/、publisher/render_html.py、render_xlsx.py、selfcheck/、
  references/ 中的 metrics-formulas / data-sources / pharma-valuation / cross-market-notes /
  output-templates / debug-playbook 均来自此处。
- **改动**：三层目录重构、路径引导（`engine/_paths.py`）、契约校验接入、
  `run_regression.py` 修复"测试目录缺失仍报 GREEN"的误导性行为、`SCHEMA_VERSION` 升至 1.1.0。

## 2. equity-researcher（Kimi 精选技能）

- **来源**：Kimi 官方精选技能市场
- **许可证**：**未随包提供任何许可证文件**。默认保留一切权利。
- **处理**：**不复制其代码与文档原文。** 本产品仅借鉴其中不受著作权保护的
  **方法、思想与事实**，具体包括：
  - 分析纪律：先还原当前价格隐含了什么假设，再陈述自己的判断
  - 三镜合一的估值架构（绝对 / 相对同业 / 相对自身历史）
  - 情景分析应包含"当前隐含情景"反推与情景转换触发条件
  - 版式惯例：表格数字等宽对齐、负数用括号、预测列加底色、标题孤行保护
    （这些是沿用数十年的卖方行业惯例，非任何人的私产）
  - 本项目审计其实现时发现的缺陷清单（属事实发现）
- **明确排除**：其 CSS、Python 脚本、markdown 文档的具体表达一律未纳入，
  样式表与框架文档均按设计原则重新编写。
- **品牌**：其内含的 "Kimi Research" 品牌标识（封面标识条、`.kimi-*` 样式类、
  免责声明发布方署名）**全部移除**，不得出现在本产品任何产出中。

## 3. xtt-investment-banking-private-equity

- **来源**：素材夹中的 Kimi 插件，作者 xutiantian
- **许可证**：随包 LICENSE 声明 **Apache License 2.0**（版权人 "Finance Plugin Suite"）。
  注：其 `kimi.plugin.json` 中 `license` 字段写作 `UNLICENSED`，与 LICENSE 文件冲突。
  本产品按其 LICENSE 文件所声明的 Apache-2.0 处理，并履行相应署名义务。
- **处理**：借鉴其数据契约与治理设计，具体包括：
  - `DataPackage` 溯源模型：`connector_status` 诚实枚举、`fallback_trail` 永远在场、
    `evidence_class` 证据分级、lineage 条目形状
  - 供应商中立的能力注册表命名思路
  - 错误码 → agent_action 的"错误域约束重试"模式
  - 财务归一化的调整桥分类学、模型审计勾稽清单
- **改动说明**（Apache-2.0 第 4(b) 条要求）：本产品未直接复用其源码文件；
  上述设计经重新实现并适配三层架构，字段命名与语义有调整。

### 3a. xtt 内含的第三方组件 —— 明确不使用

xtt 自身的 LICENSE 载有第三方声明，原文要点：其 `skills/datasource-*/scripts/`、
各 `vendor-guide.md` 及 `wind-mcp/` 来自 Moonshot AI 单独分发的数据源插件包，
许可证为 UNLICENSED，**不受 Apache-2.0 覆盖**，仅为该插件作者自用部署而转载。

**本产品不使用这些文件。** 技术上它们也无用武之地——均依赖 Kimi 私有网关。

## 4. 独立数据源插件（ifind / sp_data / tianyancha / world_bank_open_data / yahoo_finance）

- **来源**：Moonshot AI
- **许可证**：`UNLICENSED`（专有，未授予使用许可）
- **处理**：**不使用**。经 `diff` 验证与 xtt 内嵌版逐字节相同。

---

## 使用范围声明

本产品为**非营利的自用工具**，在小范围内与朋友分享。不公开发布、不商业化。

若日后改变用途（公开发布或商业化），须重新评估上述第 2、3 项的合规边界，
并就 equity-researcher 的使用范围与原作者或平台确认。

---

## 免责

本产品所有输出仅为数据分析与呈现，**不构成投资建议**。使用者应独立判断并自行承担投资风险。
