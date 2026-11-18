# 定时Agent提示词

实际执行模板为 [autonomous-collection.md](../../src/takeaway_insight/prompts/autonomous-collection.md)。新建任务会复制该模板到数据库，随后可以在网页“Agent定时任务”中独立编辑；修改源码模板不会覆盖已有任务。

worker按任务间隔启动本地agy/dsh并传入渲染后的提示词。Agent自行连接CDP、获取订单、调用数据库技能写入并独立核对，最后返回普通中文报告。启动器只记录进程状态、退出码和日志，不要求固定JSON，也不接管采集和写库。

模板变量：today/now为中国日期时间，project_dir为实际工程目录，database_skill为随包分发的技能绝对路径，run_id为运行编号，task_name/trigger/request为任务与触发信息，variables为网页中填写的任意JSON对象。额外要求request始终追加到最终提示词。未定义的模板变量会生成明确失败记录，不影响其他任务。

variables可以填写cdp_url、目标日期、门店或商家后台入口，Agent据此执行。它不会自动改写MCP配置中的CDP地址；改端口时同步修改 [.agents/mcp_config.json](../../.agents/mcp_config.json) 或 [dsh.browser.yml](../dsh.browser.yml)。默认连接http://127.0.0.1:9222。

[数据库操作步骤](local-database-access.md)与[完整权限启动方式](../agents/README.md)已与实际代码对应。真实商家采集仍需本地模型账号可用、CDP浏览器已启动和商家后台已登录。
