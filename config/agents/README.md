# 本地Agent启动方式

网页“Agent定时任务”保存执行器、任意提示词、附加参数、任务变量和间隔。`uv run takeaway worker`每5秒检查到期任务并原生启动进程；网页“立即执行”进入同一队列。`uv run takeaway agent-run --task 1`可直接运行，不依赖worker。

## Antigravity

本机核实agy 1.5.9的参数。实际启动器传入会话级完整权限settings（内容同[配置文件](antigravity.full-access.json)）、`--sandbox=false --dangerously-skip-permissions -p 提示词`。不追加运行时限或固定JSON格式。

指定Agent：任务“附加参数”填写 `["--agent", "你的Agent名称"]`。模型等原生命令参数也可在此设置。agy从工程 [.agents/mcp_config.json](../../.agents/mcp_config.json) 自动发现浏览器MCP，连接现有CDP 9222；其配置允许本地完整文件路径。

## DeepSeek Harness

实际命令为 `dsh --profile headless --permission-mode danger-full-access --approval-policy never 附加参数 -`，提示词写入标准输入。`agent-init --runner dsh`会将附加参数设为 `["--patch", "工程绝对路径/config/dsh.browser.yml"]`。网页新建dsh任务时请填写该patch路径；在自己的patch中指定所需Agent配置。dsh的指定方式按官方profile/patch机制，不虚构`--agent`参数。

[浏览器patch](../dsh.browser.yml)连接CDP，[原有stdio配置](../dsh.stdio.yml)是餐账分析查询MCP，两者作用不同。当前机器未安装dsh，因此未做该客户端实测。

## 自定义执行器与运行结果

command和arguments是JSON字符串数组，原样由create_subprocess_exec执行，不经过Shell拼接；需要Shell时可明确填写bash等。`{prompt}`会替换为最终提示词，标准输入也会传入提示词。自定义执行器的权限参数由填写的命令决定。

代码不设置工具/网址白名单、SQL只读、沙箱和Agent任务运行时限。worker停止时终止它启动的进程组并标记interrupted，不把中断说成完成。进程退出0只标记“进程已结束”，核对结论在Agent中文报告中。进程崩溃导致的旧running记录需要结合日志、PID与数据库人工核实，启动器不猜测其业务结果。

首次使用需完成模型账号登录，在CDP浏览器手动登录各商家后台。调度器不保存商家密码。本轮仅验证本地原生进程、数据库技能和网页流程，尚未真实采集商家订单。

官方依据：[Antigravity CLI](https://antigravity.google/docs/cli/reference/)、[MCP配置](https://antigravity.google/docs/mcp?tab=cli)、[dsh CLI](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)、[dsh MCP](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)。
