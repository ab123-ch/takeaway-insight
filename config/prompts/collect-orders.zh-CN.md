# 采集提示词入口

当前可执行的默认提示词只有一份：[autonomous-collection.md](../../src/takeaway_insight/prompts/autonomous-collection.md)。请阅读或复制该文件；网页新增任务时自动载入它。

由定时任务启动本地Agent，Agent通过CDP采集三个商家后台，调用数据库技能入库，然后回查数据库与后台核对。权限由实际启动参数配置，不只写在提示词里；不要求固定JSON输出。
