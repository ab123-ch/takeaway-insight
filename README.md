# 餐账 · Takeaway Insight

Python 外卖订单与商品销量工作台，面向自有门店的美团外卖、饿了么／淘宝闪购、京东秒送订单。支持本地启动、服务器部署以及标准 MCP 查询，后续可交给 DeepSeek dsh 读取经营数据、结合库存与配方分析备货。

**官方 API 接入暂不可用。** 相关代码与配置保留，但已停止实际调用；现有商家后台账号不能直接用于开放平台 API 授权。当前由定时任务调用本地agy/dsh，通过CDP浏览器自主采集、调用数据库技能入库并独立核对。饿了么官方 SDK 合同和京东待核对合同仅供后续联调参考，不能当作已接通。详见 [平台说明](docs/platforms.md)。

## 快速启动

需要本地已配置账号的agy或dsh、CDP浏览器、Node/npm，以及Python 3.12–3.14 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```bash
git clone https://github.com/ab123-ch/takeaway-insight.git
cd takeaway-insight
git switch feature/order-sync-and-mcp
uv sync --locked
uv run takeaway init
uv run takeaway agent-init --runner antigravity --enable
uv run takeaway serve
```

打开 **http://127.0.0.1:8765**。用户名默认为 `admin`；初次初始化会生成随机管理员密码、会话密钥和 MCP token，保存在本机 `.env`，权限600。请在本机查看密码；已有配置不会被覆盖。

另开终端，在同一工程目录运行Agent定时任务进程：

```bash
uv run takeaway worker
```

打开 `/agents` 编辑任务间隔、提示词及指定Agent参数。agy可填 `["--agent","名称"]`；dsh使用自己的`--patch`配置。默认提示词指向随包分发的数据库技能，Agent负责采集、入库及整体核对，启动器不要求固定JSON输出。默认浏览器MCP连接http://127.0.0.1:9222；端点及dsh配置见[执行器说明](config/agents/README.md)。

本地原生运行，agy/dsh使用完整权限、关闭沙箱和工具确认；代码不设置Agent任务运行时限或工具/网址白名单。网页仍保留管理员登录。进程退出0只代表执行器结束，数据核对结论请查看Agent日志报告。

初始数据库为空。想先体验页面，可**显式**导入演示订单：

```bash
uv run takeaway demo
```

演示门店标有“演示”，禁止平台同步；其数据与真实门店可按门店筛选区分。同步失败不会导入演示订单。

## 功能

- 工作台：日期、门店和平台筛选，商品销量、订单金额、订单数、趋势和待确认套餐数量。
- 订单明细：保留订单号、流水号、下单日期、原商品行及拆分子项，分页搜索并查看状态。
- 套餐拆分：平台结构化明细优先，门店精确匹配规则其次；顶层加号推断标记待确认，括号内的加号不拆。
- 拆分规则：数量倍数与单位、拆分预览、按门店重算历史。
- Agent定时任务：选择agy/dsh/自定义命令、指定Agent、编辑提示词与间隔、手动执行、查看日志。
- 数据库技能：查看真实表结构、任意SQL读写、复用现有订单入库与独立核对；支持美团/饿了么/京东。
- 门店管理与旧API记录：保留官方接口配置和记录，明确标记暂不可用；不再由worker轮询旧API。
- CSV 导出：中文 UTF-8 BOM 文件，保持筛选口径并处理表格公式字符。
- MCP：5个只读工具，本地 stdio 和带独立 Bearer token 的 Streamable HTTP。

“凭据已配置”只表示环境变量存在，不表示商家授权、权限和接口响应已经通过验证。当前浏览器采集使用已登录商家后台，旧API凭据仅供后续接口联调。

## 套餐如何记账

```text
门店
└─ 订单 12345（原始订单金额只记一次）
   └─ 原商品行：半只鸡+半只鸭 × 2
      ├─ 统一商品：半只鸡 → 拆分明细数量 2 份
      └─ 统一商品：半只鸭 → 拆分明细数量 2 份
```

数据库分别保存 `orders → order_lines → order_components → products`。组件通过原商品行追溯到同一订单，重复拉取按“本地门店ID＋平台订单号”更新，不重复累计。顾客姓名、电话、地址、备注不入库。

名称中的“半只”保留为商品名称，不擅自转换为0.5只或某个重量。平台对子商品数量是否已乘父套餐数量的注释不明确，结构化套餐必须先用真实样本确认 `bundle_quantity_mode`。拆分组件不均摊订单金额。

## 统计口径与备货分析

营业日期按 `Asia/Shanghai` 下单日期统计。统计已接单/已完成商品量，扣除已确认退货数量；取消和已确认全退单不计。饿了么部分退款按商品行核对；京东售后尚未接入，退款状态为 `unknown`，输出带 `warnings/coverage`，不能把其下单量当成最终净销量。

同名同单位归入统一商品；“份”和“千克”分开统计。自动按名称拆分的数量带 `needs_review` 标志，可用“仅已确认”排除；确认规则后重算历史即可更新来源。订单金额扣已知退款仅用于经营参考，**不等于平台结算收入**。

销量不是实际耗料。分析“明天进多少货”时，还需向 dsh 提供配方/出成率、可用库存、报损、采购单位、供货时间和促销等信息。本版提供可追溯的销量数据与 MCP，不生成缺少依据的采购结论。

## 接入与部署文档

| 文档 | 内容 |
| --- | --- |
| [Agent与CDP](config/agents/README.md) | 原生执行器、完整权限、指定Agent和浏览器MCP |
| [提示词与数据库技能](config/prompts/README.md) | 实际默认提示词、变量、查表/入库/核对步骤 |
| [平台接入](docs/platforms.md) | 官方来源、凭据、签名、分页、退款及套餐数量限制 |
| [本地／服务器部署](docs/deployment.md) | CLI、Compose＋PostgreSQL、systemd、HTTPS、备份和补拉 |
| [MCP 与 dsh](docs/mcp.md) | stdio / HTTP 配置、工具与查询示例 |
| [网页 API](docs/web-api.md) | 会话、CSRF、查询与管理接口 |
| [设计说明](docs/plans/2026-11-17-design.md) | 架构与业务边界 |

Agent worker推荐原生服务器部署，使用已配置模型和浏览器登录态的服务用户。提供[systemd/Nginx 示例](deploy/)；现有[Dockerfile](Dockerfile)和[compose.yaml](compose.yaml)不含agy/dsh/Node，不能直接用于完整Agent执行。web、worker 和迁移必须使用同一个数据库。Compose使用PostgreSQL前须按部署文档修改 `.env` 数据库URL；本地默认SQLite。开发机器未安装Docker，容器和PostgreSQL仍待目标环境联调。

## 开发与验证

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

测试覆盖套餐倍数、门店隔离、订单更新、退款取消、分页失败、租约接管与取消、迁移、网页登录/CSRF，以及实际 MCP SDK 协议。平台测试使用人工构造的官方合同样本，不代表真实商家已接通。dsh配置按官方插件文档提供；本机未安装dsh，未执行dsh客户端全链路联调。

Git遵循 `develop → feature/order-sync-and-mcp`。本次实现保留feature分支供审查，不自动合并。进度见 [progress.md](progress.md)。
