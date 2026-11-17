# 检索与设计依据

- 工作区没有既有外卖经营分析工程。既有 dingtalk-codex-readonly 使用 FastAPI；ship-watchtower-agent 与 dzg-ai-tools 使用 FastAPI + SQLAlchemy，可沿用成熟技术组合，不复制其业务代码。
- 用户确认采用 Python 单工程网页模板，SQLite 本地运行、PostgreSQL 服务器部署。
- 数据层：Merchant → Order → OrderLine → OrderComponent；Product 独立用于统一命名，组件保留原行和拆分规则来源。套餐头金额不在子项重复汇总。
- 每单购买 2 份“半只鸡+半只鸭”：两个组件分别为 2 份半只鸡、2 份半只鸭；不能擅自把名称中的“半”改算成整数份，也不能平均分配金额。
- 饿了么现商家平台 https://open.shop.ele.me/ 公开列出“餐饮外卖管理（商家身份）”，支持商家自研系统，说明无需保证金；应用正式访问仍需要资质审核与授权。
- 饿了么 SDK 列有 eleme.order.getAllOrders(shopId,pageNo,pageSize,date)、getOrder、mgetOrders、getRefundOrder。正式服务端签名/请求字段需由连接器任务核对官方 SDK 源码。
- 京东秒送旧平台 https://opendj.jd.com/ 公告迁移至 https://open.jd.com/，计划 2026 Q4 下线。新版列有自研商家→秒送商家→外卖。
- 旧订单目录 https://opendj.jd.com/api/getAllApiByCategory/169.htm 包含 order/es/query 和 ApiPlatformService/querySellerOrderPage。不能把旧接口路径直接冒充新版 API；需可配置且文档明确版本边界。
- 官方 MCP SDK 可用 Streamable HTTP ASGI app，挂载时父应用负责 session_manager.run() 生命周期；同时提供 stdio 工具入口。
- DeepSeek 官方项目 https://github.com/deepseek-ai/deepseek-harness；dsh 采用插件方式。实现标准 MCP 并提供官方 MCP client 插件配置示例。

## 未具备条件
- 尚无两个平台的生产应用凭证、授权 token 和脱敏真实响应样本。
- 本机 Python 3.14.2，已有 uv，无 Docker、无 dsh。
