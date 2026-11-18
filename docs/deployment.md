# 本地与服务器部署

需要Python 3.12–3.14、uv，以及实际要用的agy或dsh。浏览器MCP还需Node/npm；工程固定Chrome DevTools MCP 1.13.0，该版本支持Node 20.19+、22.12+或23+。所有命令在同一工程目录执行。

## 本地启动

```bash
uv sync --locked
uv run takeaway init
uv run takeaway agent-init --runner antigravity --enable
uv run takeaway serve
# 在另一个终端运行
uv run takeaway worker
```

打开http://127.0.0.1:8765/agents，用户名默认为admin，随机密码查看本机.env。init保留已有配置并迁移数据库，升级版本也可执行`uv run takeaway db-upgrade`。首次agent-init创建每15分钟任务；不加--enable时保持禁用，方便先编辑。再次--enable可启用已存在任务，已有执行器、提示词及参数保留。

启动前完成agy/dsh的模型账号配置，连接开启CDP的Chrome并在其中登录三个商家后台。默认端点http://127.0.0.1:9222，端点不同请同时改.agents/mcp_config.json（agy）或config/dsh.browser.yml（dsh）与任务说明。Chrome启动/CDP开启方式按实际Chrome版本使用[官方连接指南](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/advanced-usage.md)。不将商家账号填到本系统管理员登录框。

网页可编辑指定Agent参数、任意提示词、变量和间隔，手动执行也由worker启动。使用dsh时可执行`agent-init --runner dsh --enable`创建带浏览器patch的新任务；已存在任务请在网页更改执行器及参数。agy指定Agent填`["--agent","名称"]`；dsh使用自己的--patch。

无需worker的即时运行：

```bash
uv run takeaway agent-run --task 1 --request '采集指定日期并核对'
```

这条命令会实际运行本地Agent。进程日志默认data/agent-runs，网页可查看。退出0只表示原生进程结束，业务核对结果以Agent报告为准。关闭worker会中断它当前启动的Agent并记录状态；强制断电后的旧running记录需检查PID、日志和数据库。

官方API接口暂不可用，旧代码/配置保留但不执行。历史补查直接修改任务提示词/日期或用agent-run的附加要求。初始库为空，可显式`uv run takeaway demo`体验；演示订单不能代表真实业务。

## 原生服务器与systemd

推荐把工程放到/opt/takeaway-insight，使用已安装并登录agy/dsh的服务用户运行`uv sync --locked --no-dev`与初始化。参考[网页服务](../deploy/takeaway-web.service)和[Agent worker服务](../deploy/takeaway-worker.service)，按实际用户和路径调整。worker没有PrivateTmp或NoNewPrivileges这类额外执行隔离。服务用户的模型配置、技能与Chrome/CDP须可用。

systemd通常不加载交互Shell的PATH，请在.env明确设置实际可执行路径，例如TAKEAWAY_ANTIGRAVITY_EXECUTABLE=/home/takeaway/.local/bin/agy，并在服务Environment配置包含uv/node/npx的真实PATH。TAKEAWAY_AGENT_PROJECT_DIR默认当前工作目录，TAKEAWAY_AGENT_LOG_DIR默认data/agent-runs。web/worker必须使用同一数据库；子进程会继承启动器实际数据库URL，SQLite路径会转换成绝对路径。

外网访问配[Nginx示例](../deploy/nginx.conf.example)及HTTPS，设置TAKEAWAY_PUBLIC_URL和TAKEAWAY_SECURE_COOKIES=true。MCP HTTP使用独立token，网页登录使用管理员密码。

## Docker与PostgreSQL

现有Dockerfile/compose.yaml保留用于网页、迁移、PostgreSQL服务。镜像没有安装或登录agy/dsh/Node，也没有主机浏览器登录态，因此不能直接当作完整Agent部署。使用Compose提供数据库/网页时，单独原生运行worker并显式指向同一PostgreSQL；容器内的postgres:5432与主机地址不同，按实际端口/网络配置，不要给原生worker沿用容器内部地址。

当前机器没有Docker，容器和PostgreSQL仍待目标环境联调。

## 备份

SQLite运行中使用标准库backup取得一致快照：

```bash
uv run python - <<'PYTHON'
import sqlite3
from datetime import datetime
from pathlib import Path
Path('backups').mkdir(exist_ok=True)
with sqlite3.connect('data/takeaway.db') as source:
    with sqlite3.connect(f'backups/takeaway-{datetime.now():%Y%m%d-%H%M%S}.db') as target:
        source.backup(target)
PYTHON
```

PostgreSQL使用pg_dump或托管备份。迁移/恢复前停止worker并备份，恢复后核对门店、任务和订单，补采缺少日期。
