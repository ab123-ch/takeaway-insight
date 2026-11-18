# Agent访问本地数据库

完整操作说明在随Python包分发的 [takeaway-database/SKILL.md](../../src/takeaway_insight/skills/takeaway-database/SKILL.md)。调度器将该文件的实际绝对路径放入每次提示词，agy也能从工程.agents/skills发现技能。

在任务工程目录执行：

```bash
uv run takeaway-db inspect
uv run takeaway-db sql 'SELECT id,name,platform,external_shop_id,is_demo FROM merchants'
uv run takeaway-db ingest --merchant 本地门店ID --file data/本次真实订单.json
uv run takeaway-db reconcile --merchant 本地门店ID --date YYYY-MM-DD --file data/本次真实订单.json
```

启动器把自己实际连接的数据库URL通过TAKEAWAY_DATABASE_URL传给子进程，SQLite相对路径转换为绝对路径；不会因为切换工作目录而访问另一数据库。手动执行工具时由当前环境变量及工程.env决定连接。inspect显示真实表结构，不打印数据库密码。

SQL工具具有DDL/DML和查询能力，没有只读和表白名单。Agent也可以直接用本地Python/SQLAlchemy。ingest复用现有订单去重、套餐拆分和事务；reconcile以采集文件为独立基准，核对订单集合、时间/流水/金额、原行/SKU/数量及套餐子项。差异时退出码1，不能把进程正常结束直接当作数据已经核对。

三平台标识为meituan/eleme/jd。本地merchant_id与平台门店编号不同，应先确认身份。不要用演示数据补空缺，未知字段按实际合同处理，不以默认0宣称后台显示了0；需要时使用SQL记录原始事实和待核实项。表关系及字段映射详见技能和contracts.py/models.py。
