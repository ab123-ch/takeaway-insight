# 外卖经营分析工程实施计划

## 目标
实现饿了么／淘宝闪购、京东秒送订单定时同步，保留订单—商品行—拆分明细关联，提供网页销量分析、平台配置、同步记录、MCP 查询及本地/服务器运行方式。

## 授权与仓库
- 用户已批准独立 Python 工程 `/Users/chenh/PycharmProjects/takeaway-insight`。
- 分支：新仓库 `develop` 初始基线 → `feature/order-sync-and-mcp`。
- 参照智脑 GitHub 仓库 `ab123-ch/AI-brain` 的托管账号和可见性建立独立仓库；不改智脑工作区。
- 未获得合并授权，不合并 feature 至 develop/master。

## 阶段
1. [完成] 建仓、设计与服务契约；确认依赖版本和平台合同。
2. [完成] TDD 实现数据模型、同步和套餐拆分、统一查询服务。
3. [完成] 平台连接器、签名和响应解析；合同测试；实店联调限制明确记录。
4. [完成] 网页工作台、登录、门店/规则管理、CSV导出及同步记录。
5. [完成] MCP stdio + Streamable HTTP、dsh 配置说明；两种传输实际连通。
6. [完成] 本地启动、Compose/PostgreSQL 配置、迁移与运维说明；容器待目标环境验证。
7. [完成] 78项测试、Ruff/格式/JS语法/构建、实际浏览器验证、两轮独立审查与修复。
8. [进行中] 同账号公开仓库已创建，待提交和推送已验证 feature，交付。

## 验证边界
- 没有用户的两平台生产 AppKey/Secret/门店授权，不能验证真实拉单；必须使用明确标记的样例请求合同验证，并在页面显示未配置状态。
- 当前机器没有 Docker 命令；可以验证部署配置与本地运行，不能声称本机完成 Docker 容器测试。

## 错误与处理
| 问题 | 处理 |
| --- | --- |
| 用户给出的 ai-brain 目录没有 .git | 只读找到实际工作区 claw-code-parity，远端为 ab123-ch/AI-brain |
| 本机未安装 dsh | 依照 DeepSeek 官方插件文档生成接入说明，不改用户现有环境 |
| MCP 测试 structuredContent 为 None | 安装版 SDK 将无泛型 dict 视为无结构类；改用 dict[str, Any] 并显式启用 structured_output，协议测试断言 outputSchema |
