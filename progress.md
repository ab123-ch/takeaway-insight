# 实施进度

## 2026-11-17
- 已完成需求、工程位置、架构及开发分支确认。
- 已定位智脑实际 Git 远端，并保持现有工程与未提交改动不变。
- 已创建独立公开仓库 https://github.com/ab123-ch/takeaway-insight ，origin 指向该仓库，保持已确认 feature 分支。
- 已实现 FastAPI/Jinja 网页、SQLAlchemy 数据模型、Alembic 迁移、APScheduler 独立worker、两平台连接器与标准MCP。
- 套餐保存原订单/商品行/组件关联，数量用Decimal；环境变量逐店引用，丢弃顾客PII。平台无生产凭据，合同样本测试不代表实店接通。
- 核心与连接器通过独立规格、质量审查；已修复门店身份修改竞态、租约fencing、取消写库、退款数量冲突、饿了么部分退款类型、京东重复分页/小时边界及售后unknown口径。
- 实际浏览器验证：登录、订单展开、套餐2份预览各2、规则保存、56笔历史重算、新增未接入门店、同步失败明确显示缺少对应env；桌面与390px手机页面均可用，无整页横向溢出。
- 实际 MCP SDK 客户端：HTTP读取5工具/2演示门店；stdio子进程读取当日11笔演示订单，并返回JD售后warnings。dsh客户端本机未安装，未声称联调。
- wheel/sdist构建与SQLite迁移校验通过；Docker未安装，仅校验Compose YAML结构并提供部署说明。
- UI规格审查发现的顶栏旧筛选及最终质量审查发现的套餐预览异步失效问题均已修复，Node行为回归覆盖筛选传播与延迟响应；独立UI规格与整体质量复核通过。
- 最终本地验证：78 tests passed；Ruff检查与格式检查、JS语法、wheel/sdist构建通过。既有Starlette测试客户端弃用提示1条，不影响运行。Compose YAML结构通过，未运行Docker。
- 本机生成的随机密码/会话密钥/MCP token未出现在候选Git文件中；`.env`、数据库、运行截图和备份目录均被忽略。
- `9e9c529` 已提交并推送至 origin/feature/order-sync-and-mcp，develop仍为初始基线，未执行分支合并。HTTPS OAuth不含workflow scope，使用本机已有且已验证的GitHub SSH认证成功推送，未改全局Git配置或扩大token权限。

## 2026-11-18
- 按用户最终澄清改为本地定时Agent启动器，网页管理任务、任意提示词/参数、立即执行和进程日志；无消息机器人集成。
- 内置agy/dsh使用完整执行权限，启动器不设置沙箱、工具/网址白名单或任务运行时限，不要求固定JSON，不代理采集和写库。
- 增加数据库技能及inspect/sql/ingest/reconcile工具，支持任意SQL读写和三平台订单；套餐沿用原订单原商品关联。临时原生子进程完成入库和独立核对。
- 原官方API保留并标记暂不可用，worker停止轮询旧API。新增迁移002与CDP浏览器MCP配置，技能/提示词随wheel分发。
- 独立审查发现的模板错误阻塞队列、日志路径失败挂起、核对漏关键字段/无门店假成功均已复现并补回归修复；CLI重复enable生效。补充验证SIGTERM正常收尾及重复商品行ID核对。
- 本地102项测试通过，Ruff与格式/JS检查通过；实际网页创建/编辑/排队/worker执行/查看日志、390px无整页横向溢出且无JS错误。浏览器MCP初始化成功，33工具含evaluate_script。
- 真实模型与三个商家后台尚未联调，未采集生产订单；本机dsh/Docker未安装。保留已确认feature分支，不合并。
