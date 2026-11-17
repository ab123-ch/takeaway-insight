# MCP 与 DeepSeek dsh

餐账使用官方 MCP Python SDK，提供 stdio 与 Streamable HTTP。MCP 只查询已入库数据，不替你开通外卖平台权限，也不调用接单、退款或采购接口。

## 工具

| 工具 | 用途 |
| --- | --- |
| `list_merchants` | 门店编号、平台和配置状态 |
| `get_sync_status` | 同步新鲜度及失败原因 |
| `get_orders` | 带原始商品行、套餐组件的分页订单 |
| `get_sales_summary` | 日期/平台/门店销量、汇总与趋势 |
| `get_product_trend` | 指定商品的日销量 |

所有日期使用 `YYYY-MM-DD`，归属门店本地时区下单日。最多一次查询367天，订单每页最多100条，通过 `total` 决定是否继续翻页。数量和金额是十进制字符串；不同单位不自动相加换算。`needs_review=true` 表示名称推断的套餐，需要人工确认规则。确认口径可用 `confirmed_only=true` 排除未确认组件。

分析必须读取 `warnings/coverage`：京东尚未接入退款售后明细，`refund_status=unknown`，数量为有效订单原始下单量，不能报告为最终退款后净销量。没有成功同步记录不能断言某天没有订单。订单金额不是平台结算收入。

## 本地 stdio

先完成 `uv sync`、`uv run takeaway init`，在项目目录运行 `uv run takeaway-mcp`。stdout 只用于 MCP 协议，不要将其他输出重定向进去。

DSH 使用 Cordis YAML 插件行，不是 `mcpServers` JSON。复制 `config/dsh.stdio.yml` 到自己的配置位置，把 `/absolute/project` 替换为本工程路径：

```bash
dsh --profile web --patch /absolute/project/config/dsh.stdio.yml
```

如果 `uv` 不在 DSH 的 PATH，填写 `uv` 的绝对路径。stdio 子进程不应假设继承了所有环境变量；`cwd` 和 `--directory` 确保程序从本项目 `.env` 读取配置。

## HTTP

启动网页服务后，MCP 地址是 `http://127.0.0.1:8765/mcp`（无尾部斜杠）。配置 `.env` 中的独立 `TAKEAWAY_MCP_TOKEN`；网页登录态不替代 MCP token。

复制 `config/dsh.http.yml`。让 **DSH 主进程** 继承与服务端相同的 `TAKEAWAY_MCP_TOKEN`，再用 `--patch` 指定该配置。服务器部署时将 URL 改成自己的 HTTPS 域名，并设置服务端 `TAKEAWAY_PUBLIC_URL`。

两个示例按需启用一个，避免同一作用域重复注册 `serverName: takeaway`。

## 兼容性依据与边界

2026-11-17 从官方发布包核对 dsh 0.2.2-rc.1 与 MCP client 同版配置支持 `stdio`、`streamable-http`、`command/args/cwd`、`url/headers`。示例中的 `!!js` 环境变量写法来自官方 MCP client 文档，避免把 token 写进提交的配置文件。

不要单独盲装 `@deepseek-ai/dsh-mcp-client@latest`：本次核对时该包 latest 与 dsh 自带版本不同。优先使用 dsh 自带的 MCP client。0.2.2-rc.3/master 的 `expandTemplates` 与 `${credential:...}` 能力不适用于所有已发布版本，因此示例不依赖它。

本工程已用实际 MCP SDK 客户端连接本地 stdio 子进程与运行中的 HTTP 服务，验证工具列表与查询、无token拒绝及独立鉴权。本机没有安装dsh，未执行 DSH 客户端端到端验证。升级 dsh 后应在其工具列表中确认 `mcp__takeaway__get_sales_summary` 等工具可用。

官方参考：

- [DeepSeek MCP client 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)
- [DSH CLI profile/patch](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)
- [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)

## 分析示例

“先检查门店最近同步是否成功，再比较最近四个周二各菜品销量，列出套餐推断未确认的数量。根据我提供的配方、现存库存、预计到货量和保质期，估算明天的进货量，并列出假设和数据缺口。”

销量只能帮助估计需求。取消、退款、实际制作和物料回库不等价；未接入的渠道销量、报损、配方和库存均不会被系统凭空补齐。
