# 接入你自己的付费终端

免费数据源开箱即用，无需任何配置。本文档面向**手上有付费终端 key 或导出文件**、想要更高数据精度的用户。

接入后，付费源自动升为 Tier-0（降级链链首），并解锁免费源拿不到的字段：一致预期、分部收入、完整季度财报、可比公司全量倍数。

**一条纪律**：付费不等于免信任。付费源升 Tier-0，但价格与市值仍会与免费源交叉校验——这道防线不因数据源付费而关闭。

---

## 三种接入方式

### 方式一：API token（最常见）

如果你的终端提供 API（如 Tushare Pro），只需设一个环境变量：

```bash
export TUSHARE_TOKEN=你的token          # Tushare Pro
export IFIND_TOKEN=你的token            # 同花顺 iFinD
```

内置适配器已就位（`engine/providers/paid/adapters/` 下），设好变量后验证：

```bash
python engine/providers/paid/registry.py
```

它会列出所有认证就绪的适配器。你的终端出现在列表里就算接通了。

### 方式二：导出文件（无需 API）

手上有 Wind / iFind / 同花顺 / Choice 导出的财报表（xlsx 或 csv）？直接在对话里把文件发给工具，或设：

```bash
export ERS_IMPORT_FILE=/路径/到/你的导出.xlsx
```

`wind_export.yaml` 适配器会用中英别名匹配表头，两种常见布局（每行一个期间 / 字段为行期间为列）都能吃。

### 方式三：本地终端客户端

Bloomberg 等须经本地 Terminal 的，需要一个把终端查询封装成命令行的桥接脚本。`bloomberg.yaml` 已备好字段映射，把桥接脚本路径放进 `BLOOMBERG_BRIDGE` 即可。

---

## 接你的终端（内置列表没有的）

复制模板，按注释填写，放回原目录，**不改任何代码**：

```bash
cp engine/providers/paid/adapters/custom.template.yaml \
   engine/providers/paid/adapters/我的终端.yaml
# 编辑 我的终端.yaml
python engine/providers/paid/registry.py    # 验证
```

需要填的只有四块：

| 块 | 填什么 |
|---|---|
| `auth` | key 放在哪个环境变量 / 文件路径 |
| `transport.endpoints` | 每种数据的请求 URL（`{symbol}` `{token}` 会自动替换） |
| `capabilities` | 这个源能提供哪些数据 |
| `fields` | 标准字段 → 上游字段名 + **原始单位** |

### 单位一定要填对

工具靠 `unit` 把 元/万/亿 统一成内部口径。填错会怎样？举个真实例子：如果把"元"计价的市值标成"百万"，市值会被当成小 100 万倍，PE/PB/PS 全错。

好在有**量纲哨兵**兜底：市值÷价格得出的隐含股本若与申报股本偏离超 1000 倍，工具直接报 critical 并拒绝出报告。但最好一开始就填对——`unit` 的可选值见 `engine/_util.py` 的 `UNIT_FACTORS`（元/千/万/百万/亿/thousand/million/billion 等）。每股价格类字段填 `native`（不换算）。

### 首次接入务必核对

内置适配器（tushare_pro / ifind / wind / bloomberg）的字段映射是**按各家公开文档编写**的，接口字段偶有调整。首次接入时跑一次真实标的，核对市值、营收、净利润这几个数与终端界面一致，再放心使用。

---

## 常见问题

**Q：设了环境变量但 registry 没认出来？**
检查变量名与 YAML 里 `auth.env_var` 是否一致；确认变量在当前 shell 生效（`echo $你的变量名`）。

**Q：接了付费源，报告里怎么标？**
`analysis.json` 的 `meta` 会记 `data_tier`（0=付费）与 `paid_adapter`（源名），报告首页据此诚实标明"本报告基于 Wind Tier-0"。

**Q：付费源和免费源都在，会不会打架？**
不会。付费源在链首优先，缺的字段由免费源逐字段补上，每个字段记录实际来源。价格和市值仍双源校验。

**Q：我不想让某个内置适配器生效？**
把对应 yaml 改名加 `.disabled` 后缀，或删掉即可。
