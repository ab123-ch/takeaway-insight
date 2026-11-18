---
name: takeaway-database
description: 查看和读写餐账本地SQLite/PostgreSQL，导入真实平台订单并核对原商品及套餐明细；用于采集后的入库和整体对账。
---

# 餐账数据库技能

在本次任务给出的工程目录执行 `uv run takeaway-db`。实际数据库取当前进程TAKEAWAY_DATABASE_URL，缺省时由工程.env加载。不要切换到另一个有数据的库；数据库访问具有完整读写权限，下面工具不设置SQL只读或表白名单，也可以直接使用Python/SQLAlchemy。

## 查看数据库

```bash
uv run takeaway-db inspect
uv run takeaway-db sql 'SELECT id,name,platform,external_shop_id,is_demo FROM merchants'
```

关系：merchants.id→orders.merchant_id→order_lines.order_id→order_components.line_id；组件product_id关联products.id。split_rules为门店拆分规则，sync_runs为旧接口记录，agent_tasks/agent_runs为Agent调度记录。演示门店is_demo=true。

## 写入订单

核对真实门店。美团platform=meituan、饿了么=eleme、京东=jd；本地merchant_id与平台门店号是两个不同值。缺少门店时可通过InsightService.create_merchant创建，也可按实际表结构使用SQL。

将真实数据映射为工程contracts.py中的NormalizedOrder数组，保存到本次运行的data目录文件，再执行：

```bash
uv run takeaway-db ingest --merchant 真实本地门店ID --file data/本次订单.json
```

该工具调用已有InsightService，整个批次事务入库并按门店+订单号更新。原始商品行包括external_id/name/quantity，套餐子项components只在数量口径已核实后填写，纯名称套餐可交给现有拆分规则。原商品行没有平台ID时，可以根据商品名、SKU、单位和同项序号定义稳定的本地标识，重复运行必须使用同一规则，不能随机生成。金额和数量使用十进制字符串，时间带时区。

可以直接读取contracts.py、services.py、models.py，按实际数据采用SQL或现有服务操作。完整执行权限不要求一律使用上述封装。未知信息不能伪造，表关系和业务数量必须保持真实。

## 独立核对

采集得到的完整订单数组是核对基准，不能从数据库反向生成“期望结果”。入库完成后重新查询数据库：

```bash
uv run takeaway-db reconcile --merchant 真实本地门店ID --date YYYY-MM-DD --file data/本次订单.json
```

该命令比较原始订单号集合、流水号、下单/完成/更新时间、金额、原商品行/SKU/单价及数量、退款状态/数量和按当前规则预期的套餐组件；差异返回ok=false及具体订单/行，并用退出码1表示未核对一致。还应核对商家后台显示的订单总数及筛选范围，不能仅凭插入函数返回值认定完整。

有差异就查询定位并按真实来源修正，再核对。最终向用户报告实际已写入量、核对结果、仍缺少的登录/数据，不能把Agent正常退出等同于商家数据已经核对成功。
