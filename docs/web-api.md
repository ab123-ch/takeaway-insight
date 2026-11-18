# 网页交互合同

所有 `/api/*` 需要网页登录会话。GET `/api/session` 返回 `username,csrf_token,today,mcp_http_enabled`。写请求加 `X-CSRF-Token`，JSON 提交；失败读取 `detail`，422 detail 可能是校验错误数组。没有登录401；CSRF403；重复门店/规则409。数值数量和金额以十进制字符串返回。

| 方法 / 路径 | 输入与输出 |
| --- | --- |
| GET `/api/merchants` | `{items:[{id,name,platform,external_shop_id,timezone,enabled,is_demo,sync_interval_minutes,configured,missing_configuration,next_sync_at,connector_status,configuration}]}`；configured只表示凭据存在，连接状态仍待真实联调 |
| POST `/api/merchants` | name,platform=meituan/eleme/jd,external_shop_id,timezone=Asia/Shanghai,enabled=false,sync_interval_minutes=15,configuration={}；201返回门店 |
| PUT `/api/merchants/{id}` | 完整同上输入；演示标识不可编辑 |
| GET `/api/sales` | start_date,end_date 必填；merchant_id/platform/confirmed_only 可选；返回 summary,products,trend,methodology |
| GET `/api/orders` | 相同日期/门店/平台；status,query,page=1,page_size=25可选；返回 items,total,page,page_size |
| GET `/api/rules` | merchant_id可选；`{items:[{id,merchant_id,match_name,enabled,components:[{name,factor,unit}]}]}` |
| POST `/api/rules`、PUT `/api/rules/{id}` | merchant_id,match_name,enabled,components:[{name,factor,unit}] |
| POST `/api/rules/preview` | name,quantity,components可选；空组件按顶层加号推断；返回`{items:[{name,quantity,unit,source,needs_review}]}` |
| POST `/api/merchants/{id}/reprocess` | 空请求；返回processed，重算该门店历史拆分，原单不变 |
| POST `/api/sync` | merchant_id,start_date,end_date，最多31天；202后台执行，查看同步日志；演示门店不能同步 |
| GET `/api/sync-runs` | merchant_id、limit可选；`{items:[{id,merchant_id,merchant_name,platform,target_date,status,started_at,finished_at,fetched,inserted,updated,error}]}` |
| GET `/api/export/sales.csv` | 与sales相同筛选，下载UTF-8 BOM CSV |
| POST `/logout` | CSRF header，返回ok |

Sales summary: order_count,active_order_count,completed_count,cancelled_count,refunded_order_count,item_quantity,review_quantity,revenue,product_count,quantity_by_unit。
quantity_by_unit = [{unit,quantity,review_quantity}]，不同单位不得相加；多单位时item_quantity/review_quantity为null。trend=[{date,unit,quantity}]，按单位分组显示。
products=[{product_id,name,unit,ordered_quantity,sold_quantity,refunded_quantity,review_quantity,order_count}]。
金额只是订单金额扣已知退款，不等于平台结算入账，也不分配到套餐组件。

Order items: id,external_id,serial_no,merchant_id,merchant_name,platform,business_date,placed_at,status,refund_status,total_amount,refund_amount,synced_at,lines。
lines=[{id,external_id,name,quantity,refunded_quantity,unit,sku_id,components:[{id,product_id,name,quantity,refunded_quantity,unit,source,needs_review,rule_id}]}]。
状态pending/accepted/completed/cancelled；退款none/partial/full/unknown。unknown 表示尚未对账，不是没有退款。

sales、orders、product_trend均提供warnings和coverage.refunds。京东为not_integrated，统计原始有效订单下单量，不能作为最终退款后净销量；summary.unreconciled_order_count记录退款状态未知的订单数。CSV同时保存筛选范围和售后口径。当前三平台门店时区仅支持Asia/Shanghai。

门店configuration只能填写环境变量**名称**而非密钥：app_key_env,app_secret_env,access_token_env。bundle_quantity_mode=per_parent/order_total 需要先按真实平台套餐样本确认，空时结构化套餐显式失败。其他平台模式字段见platforms.md。网页不能输入原始AppSecret/Token。

模板上下文：page为dashboard/agents/orders/sales/rules/merchants/syncs/mcp-guide；csrf_token，username。已有模板和静态资源可由前端替换；不使用外部CDN。视图URL由服务端返回统一app.html。订单需明确展示同一原单/原行下的多个组件。演示门店与真实门店筛选结果必须明显区分。

## Agent任务接口

以下接口同样要求登录；POST/PUT需要CSRF。

| 方法 / 路径 | 作用 |
| --- | --- |
| GET `/api/agent-info` | 默认提示词、工程目录、本地可执行路径及权限模式 |
| GET/POST `/api/agent-tasks` | 列表/新增任务；POST返回201 |
| PUT `/api/agent-tasks/{id}` | 编辑完整任务配置，重置下次触发时间 |
| POST `/api/agent-tasks/{id}/run` | body为`{"request":"附加要求"}`，202返回排队记录；worker执行 |
| GET `/api/agent-runs` | 运行状态、退出码、时间、日志路径 |
| GET `/api/agent-runs/{id}` | 包含本次命令与提示词快照 |
| GET `/api/agent-runs/{id}/log` | 纯文本日志或明确的准备失败原因 |

任务字段：name，runner=antigravity/dsh/custom，command字符串数组，arguments字符串数组，prompt，variables对象，enabled，interval_minutes（启用时为正整数）。自定义命令必须非空。没有任务时长和工具限制。status=queued/running/completed/failed/interrupted；completed仅代表进程退出0，数据核对结论在Agent报告中。旧/api/sync保留但调用明确返回暂不可用的失败记录。
