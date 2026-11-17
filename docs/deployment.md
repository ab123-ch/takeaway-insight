# 本地与服务器部署

依赖 Python 3.12–3.14。推荐使用 `uv sync --locked` 创建 Python 环境。初次启动在项目目录运行 `uv run takeaway init`，会生成权限为600的 `.env` 与随机管理员密码、会话密钥、独立MCP token，并执行 Alembic 迁移。已有 `.env` 保留，不覆盖。所有命令在同一项目工作目录执行，保证 SQLite 和 `.env` 指向同一位置。

## 本地

```bash
uv sync --locked
uv run takeaway init
uv run takeaway serve
# 另开一个终端，在同一目录启动定时同步
uv run takeaway worker
```

浏览器打开 http://127.0.0.1:8765，用户名默认为admin，密码查看本机 `.env` 中的 `TAKEAWAY_ADMIN_PASSWORD`。可选执行 `uv run takeaway demo` 生成明确标记的演示门店与最近8天订单；重复执行同一日期不会重复订单。演示门店禁止平台同步。真实门店与演示门店会分别显示，请按门店筛选分析。

在 `.env` 填入每店独立的 AppKey/AppSecret/AccessToken，再在网页“门店接入”填写**环境变量名称**与门店ID。更改环境后重启web/worker；本地`.env`不会覆盖进程已有环境变量。凭据存在不代表权限或API联调通过。平台模式、分页和套餐口径见 [platforms.md](platforms.md)。

手动补拉可在网页选择门店与日期，或使用：

```bash
uv run takeaway sync --merchant 1 --date 2026-11-16 --end-date 2026-11-17
```

单次范围最多31天，逐日处理；失败或门店忙时停止后续日期。单日先取得所有分页，再事务入库；同门店+订单号更新，不重复累计。同步锁通过数据库租约及心跳协调，不依赖进程内锁。

独立worker每30秒检查到期门店，按每店配置间隔同步该店中国日期订单；跨日首次会补查上一成功营业日为昨天的门店，再查今天。刚启动且从未成功同步的门店只查今天，停机多日不会自动补全历史。定期手动补查历史用于前几天售后对账。当前未实现自动OAuth授权/刷新、旧订单售后事件订阅。

## Docker Compose + PostgreSQL

首次部署前生成并编辑 `.env`：

```dotenv
POSTGRES_PASSWORD=替换为随机字母数字密码
TAKEAWAY_DATABASE_URL=postgresql+psycopg://takeaway:同一个数据库密码@postgres:5432/takeaway
TAKEAWAY_PUBLIC_URL=https://takeaway.example.com
TAKEAWAY_SECURE_COOKIES=true
```

密码含URL特殊字符时须对数据库URL中的密码部分编码。`postgres` 是Compose内部服务名；直接在主机部署时改成真实数据库地址。数据库URL必须显式配置为PostgreSQL，不能沿用本地SQLite值，否则各容器会产生独立数据库。

```bash
docker compose config --quiet
docker compose up -d --build
docker compose logs -f web worker
```

迁移由单独 `migrate` 服务先执行；web与worker随后启动。版本升级先备份数据库，再停worker、运行单次迁移、更新web/worker，避免迁移与拉单竞争。镜像只复制源码与锁文件，不包含本机`.env`和SQLite。应用以非root用户运行，数据库数据卷持久保存，数据库端口不暴露到主机。

配置 [Nginx示例](../deploy/nginx.conf.example) 的域名与TLS证书。`TAKEAWAY_PUBLIC_URL`必须与实际HTTPS来源一致，供MCP校验Host/Origin；网页会话开启secure cookie。外网只发布HTTPS入口。MCP使用独立Bearer token，不使用管理员密码或网页登录cookie。

当前开发机器没有Docker，交付仅包含可执行配置，未声称已经完成容器或PostgreSQL联调。

## systemd

把工程放到 `/opt/takeaway-insight`，创建专用系统用户takeaway，执行 `uv sync --locked --no-dev`、`takeaway init` 并配置数据库。参考 [web服务](../deploy/takeaway-web.service) 与 [worker服务](../deploy/takeaway-worker.service)，按实际路径调整后由运维安装。两个服务必须共用工作目录与数据库；也可使用单机SQLite，小规模运行时只有一个worker。

## 备份与验证

SQLite运行中使用Python标准库backup API取得一致快照，不直接复制WAL中的单个文件：

```bash
uv run python - <<'PY'
import sqlite3
from datetime import datetime
from pathlib import Path
Path('backups').mkdir(exist_ok=True)
with sqlite3.connect('data/takeaway.db') as source:
    with sqlite3.connect(f'backups/takeaway-{datetime.now():%Y%m%d-%H%M%S}.db') as target:
        source.backup(target)
PY
```

PostgreSQL用 `pg_dump` 或数据库托管服务的备份能力；备份数据库以及私有部署环境配置，分别保管。恢复前停web/worker，恢复后核对门店与同步记录，再补拉缺失日期。

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

连接器测试使用人工构造的合同样本。拿到真实授权后，先用小范围门店分别验证：完整页数/空日、订单时间、两份套餐的子项倍数、取消、整退/部分退款、商品调整、分页边界和限流；再启用定时开关。京东响应分页和售后合同存在待核实项，参见平台说明。
