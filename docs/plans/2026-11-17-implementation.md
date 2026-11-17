# 外卖订单与 MCP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 交付可本地运行、服务器部署、按门店定时同步两平台订单的 Python 网页工程及 MCP 数据服务。

**Architecture:** 平台适配器转换为标准化订单合同，应用服务执行去重、拆分与持久化。网页、REST API、MCP 共用查询口径，独立 worker 调度同步。

**Tech Stack:** Python 3.12+、FastAPI、SQLAlchemy 2、Jinja2、APScheduler 3、官方 MCP SDK、SQLite/PostgreSQL、pytest、ruff、uv。

---

### Task 1: 基础合同与数据库
- Create: `pyproject.toml`, `src/takeaway_insight/contracts.py`, `config.py`, `db.py`, `models.py`。
- Test first: `tests/test_storage.py` 验证跨平台订单去重、外键、Decimal 和 UTC/本地营业日。
- RED: `uv run pytest tests/test_storage.py`；实现后 GREEN，并运行 ruff。

### Task 2: 平台连接器（独立实现代理）
- Create: `src/takeaway_insight/providers/`、`tests/test_providers.py`、`docs/platforms.md`。
- 按 contracts.py 实现授权 HTTP 请求、签名、响应解析、分页和显式失败；只读数据请求。
- 使用 httpx.MockTransport 的固定脱敏请求/响应合同验证，不能伪造生产测试通过。

### Task 3: 订单应用服务、套餐规则、同步与报表
- Create: `services.py`, `splitting.py`, `sync.py`, `worker.py`。
- Test first: `tests/test_sales.py`, `tests/test_splitting.py`, `tests/test_sync.py`。
- 验证“2份半只鸡+半只鸭”两组件各2份，结构化套餐优先；重复同步和部分失败不能重复计数/伪装成功。

### Task 4: 网页、HTTP 和管理
- Create: `web.py`, `templates/`, `static/`。
- Test first: `tests/test_web.py` 验证登录、门店隔离、查询、规则管理、同步接口与错误。
- 本地静态资源实现响应式工作台，支持原单与拆分视图、日期门店筛选、CSV 导出和明确空状态。

### Task 5: MCP 与 dsh
- Create: `mcp_server.py`, `docs/mcp.md`、MCP 配置示例。
- Test first: `tests/test_mcp.py` 通过实际 SDK 会话列工具、调用销量/订单查询；支持 stdio 和挂载 Streamable HTTP。
- 只读工具对门店/时间参数做精确过滤，调用共用应用服务。

### Task 6: 本地与服务器运行
- Create: `cli.py`, `Dockerfile`, `compose.yaml`, `.env.example`, `deploy/`、数据库迁移脚本和 `README.md` 完整说明。
- 复用 uv 锁依赖，本地 serve/worker 命令，服务器应用/worker 独立进程与 PostgreSQL。
- 测试显式演示导入、CLI 启动、配置缺失错误和迁移。

### Task 7: 审查、验证和交付
- 执行 `uv run pytest`、`uv run ruff check .`、实际 HTTP/MCP 冒烟、浏览器检查。
- 独立代理先审需求符合性，再审代码质量，修复后重新验证。
- 更新 findings/progress，中文提交；按用户要求建立同 GitHub 账号、相同可见性的独立仓库，推送 develop 初始基线和 feature，不合并。
