"""网页会话、只读分析 API 与独立鉴权的 MCP HTTP。"""

import csv
import io
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route

from takeaway_insight.agent_jobs import DEFAULT_PROMPT, AgentJobs, AgentTaskInput
from takeaway_insight.config import PACKAGE_DIR, Settings
from takeaway_insight.contracts import NormalizedLine
from takeaway_insight.db import Database
from takeaway_insight.mcp_server import create_mcp_server
from takeaway_insight.services import InsightService, MerchantInput, RuleInput, number
from takeaway_insight.splitting import split_line
from takeaway_insight.sync import SyncRunner


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            expected = f"Bearer {self.token}".encode()
            actual = headers.get(b"authorization", b"")
            if len(self.token) < 32:
                return await JSONResponse({"detail": "MCP HTTP 未配置至少32字符的独立 token"}, 503)(
                    scope, receive, send
                )
            if not secrets.compare_digest(actual, expected):
                return await JSONResponse(
                    {"detail": "MCP 需要独立 Bearer token"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
        # SDK 子应用的唯一端点为 /；精确路由兼容 /mcp 与 /mcp/，不依赖客户端重定向。
        child_scope = dict(scope, path="/", raw_path=b"/", root_path="")
        await self.app(child_scope, receive, send)


class SyncInput(BaseModel):
    merchant_id: int
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def range_limit(self):
        if self.end_date < self.start_date or (self.end_date - self.start_date).days > 30:
            raise ValueError("补拉范围最多31天，结束日期不能早于开始日期")
        return self


class PreviewInput(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    components: list[dict] = Field(default_factory=list)


def csv_cell(value):
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def create_app(settings: Settings, database: Database | None = None) -> FastAPI:
    settings.require_web_secrets()
    owns_database = database is None
    database = database or Database(settings.database_url)
    service = InsightService(database)
    runner = SyncRunner(service, settings)
    agent_jobs = AgentJobs(database, settings)
    mcp = create_mcp_server(service, settings)
    mcp_http = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        if owns_database:
            database.dispose()

    app = FastAPI(title="餐账", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service
    app.state.runner = runner
    app.state.agent_jobs = agent_jobs
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.secure_cookies,
        session_cookie="takeaway_session",
        max_age=43200,
    )
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def bad_request(request, error):
        return JSONResponse({"detail": str(error)}, 400)

    @app.exception_handler(IntegrityError)
    async def conflict(request, error):
        return JSONResponse({"detail": "记录已存在，请检查同平台门店编号或门店内的规则名称"}, 409)

    def require_session(request: Request):
        if not request.session.get("authenticated"):
            raise HTTPException(401, "请先登录")
        return request.session

    def require_write(request: Request):
        session = require_session(request)
        actual = request.headers.get("X-CSRF-Token", "")
        if not actual or not secrets.compare_digest(actual, session.get("csrf_token", "")):
            raise HTTPException(403, "页面校验已失效，请刷新后重试")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse("/", 303)
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": request.session["csrf_token"], "error": ""},
        )

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        token = str(form.get("csrf_token", ""))
        if not token or not secrets.compare_digest(token, request.session.get("csrf_token", "")):
            raise HTTPException(403, "登录页面已失效，请重新打开")
        valid = secrets.compare_digest(
            str(form.get("username", "")).encode(), settings.admin_username.encode()
        )
        valid &= secrets.compare_digest(
            str(form.get("password", "")).encode(), settings.admin_password.encode()
        )
        if not valid:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"csrf_token": token, "error": "用户名或密码错误"},
                status_code=401,
            )
        request.session.clear()
        request.session.update(authenticated=True, csrf_token=secrets.token_urlsafe(32))
        return RedirectResponse("/", 303)

    @app.post("/logout", dependencies=[Depends(require_write)])
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/session", dependencies=[Depends(require_session)])
    def session_info(request: Request):
        return {
            "username": settings.admin_username,
            "csrf_token": request.session["csrf_token"],
            "today": datetime.now(ZoneInfo(settings.timezone)).date().isoformat(),
            "mcp_http_enabled": len(settings.mcp_token) >= 32,
        }

    @app.get("/api/agent-info", dependencies=[Depends(require_session)])
    def agent_info():
        return {
            "default_prompt": DEFAULT_PROMPT,
            "project_dir": str(agent_jobs.project_dir),
            "executables": {
                "antigravity": shutil.which(settings.antigravity_executable),
                "dsh": shutil.which(settings.dsh_executable),
            },
            "execution_mode": "full-access",
            "sandbox": False,
        }

    @app.get("/api/agent-tasks", dependencies=[Depends(require_session)])
    def agent_tasks():
        return {"items": agent_jobs.list_tasks()}

    @app.post("/api/agent-tasks", status_code=201, dependencies=[Depends(require_write)])
    def create_agent_task(data: AgentTaskInput):
        return agent_jobs.create_task(data)

    @app.put("/api/agent-tasks/{task_id}", dependencies=[Depends(require_write)])
    def update_agent_task(task_id: int, data: AgentTaskInput):
        return agent_jobs.update_task(task_id, data)

    @app.post(
        "/api/agent-tasks/{task_id}/run", status_code=202, dependencies=[Depends(require_write)]
    )
    def start_agent(task_id: int, data: dict):
        return agent_jobs.enqueue(task_id, request=str(data.get("request", "")))

    @app.get("/api/agent-runs", dependencies=[Depends(require_session)])
    def agent_runs():
        return {"items": agent_jobs.list_runs()}

    @app.get("/api/agent-runs/{run_id}", dependencies=[Depends(require_session)])
    def agent_run(run_id: str):
        return agent_jobs.get_run(run_id)

    @app.get(
        "/api/agent-runs/{run_id}/log",
        response_class=PlainTextResponse,
        dependencies=[Depends(require_session)],
    )
    def agent_log(run_id: str):
        return agent_jobs.read_log(run_id)

    @app.get("/api/merchants", dependencies=[Depends(require_session)])
    def merchants():
        return {"items": service.list_merchants()}

    @app.post("/api/merchants", status_code=201, dependencies=[Depends(require_write)])
    def create_merchant(data: MerchantInput):
        return service.create_merchant(**data.model_dump())

    @app.put("/api/merchants/{merchant_id}", dependencies=[Depends(require_write)])
    def update_merchant(merchant_id: int, data: MerchantInput):
        return service.update_merchant(merchant_id, **data.model_dump())

    @app.get("/api/sales", dependencies=[Depends(require_session)])
    def sales(
        start_date: date,
        end_date: date,
        merchant_id: int | None = None,
        platform: str | None = None,
        confirmed_only: bool = False,
    ):
        return service.get_sales_summary(
            start_date, end_date, merchant_id, platform, confirmed_only
        )

    @app.get("/api/orders", dependencies=[Depends(require_session)])
    def orders(
        start_date: date,
        end_date: date,
        merchant_id: int | None = None,
        platform: str | None = None,
        status: str | None = None,
        query: str = "",
        page: int = 1,
        page_size: int = 25,
    ):
        return service.get_orders(
            start_date, end_date, merchant_id, platform, status, query, page, page_size
        )

    @app.get("/api/rules", dependencies=[Depends(require_session)])
    def rules(merchant_id: int | None = None):
        return {"items": service.list_rules(merchant_id)}

    @app.post("/api/rules", status_code=201, dependencies=[Depends(require_write)])
    def create_rule(data: RuleInput):
        return service.create_rule(**data.model_dump())

    @app.put("/api/rules/{rule_id}", dependencies=[Depends(require_write)])
    def update_rule(rule_id: int, data: RuleInput):
        return service.update_rule(rule_id, **data.model_dump())

    @app.post("/api/rules/preview", dependencies=[Depends(require_write)])
    def preview(data: PreviewInput):
        line = NormalizedLine(external_id="preview", name=data.name, quantity=data.quantity)
        rule = (
            RuleInput(merchant_id=0, match_name=data.name, components=data.components)
            if data.components
            else None
        )
        rules = (
            [
                {
                    "match_name": data.name,
                    "components": rule.model_dump(mode="json")["components"],
                    "enabled": True,
                }
            ]
            if rule
            else []
        )
        return {
            "items": [
                {
                    "name": part.name,
                    "quantity": number(part.quantity),
                    "unit": part.unit,
                    "source": part.source,
                    "needs_review": part.needs_review,
                }
                for part in split_line(line, rules)
            ]
        }

    @app.post("/api/merchants/{merchant_id}/reprocess", dependencies=[Depends(require_write)])
    def reprocess(merchant_id: int):
        return {"processed": service.reprocess_orders(merchant_id)}

    @app.get("/api/sync-runs", dependencies=[Depends(require_session)])
    def sync_runs(merchant_id: int | None = None, limit: int = 30):
        return {"items": service.get_sync_status(merchant_id, limit)}

    @app.post("/api/sync", status_code=202, dependencies=[Depends(require_write)])
    def sync(data: SyncInput, tasks: BackgroundTasks):
        service.get_connection(data.merchant_id)
        tasks.add_task(runner.sync_range, data.merchant_id, data.start_date, data.end_date)
        return {"status": "scheduled", "message": "已提交同步，请查看同步记录"}

    @app.get("/api/export/sales.csv", dependencies=[Depends(require_session)])
    def export_sales(
        start_date: date,
        end_date: date,
        merchant_id: int | None = None,
        platform: str | None = None,
        confirmed_only: bool = False,
    ):
        result = service.get_sales_summary(
            start_date, end_date, merchant_id, platform, confirmed_only
        )
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "商品名称",
                "单位",
                "统计商品量",
                "下单量",
                "已知退货量",
                "待确认商品量",
                "关联订单数",
                "开始日期",
                "结束日期",
                "门店ID范围",
                "平台范围",
                "统计与售后口径",
            ]
        )
        for row in result["products"]:
            writer.writerow(
                [
                    csv_cell(row[key])
                    for key in (
                        "name",
                        "unit",
                        "sold_quantity",
                        "ordered_quantity",
                        "refunded_quantity",
                        "review_quantity",
                        "order_count",
                    )
                ]
                + [
                    start_date.isoformat(),
                    end_date.isoformat(),
                    merchant_id or "全部",
                    platform or "全部",
                    csv_cell("；".join(result["warnings"]) or result["methodology"]),
                ]
            )
        return Response(
            ("\ufeff" + stream.getvalue()).encode(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="sales-{start_date}-{end_date}.csv"'
            },
        )

    mcp_endpoint = BearerAuth(mcp_http, settings.mcp_token)
    for path in ("/mcp", "/mcp/"):
        app.router.routes.append(
            Route(path, mcp_endpoint, methods=["GET", "POST", "DELETE", "OPTIONS"])
        )

    @app.get("/{page:path}", response_class=HTMLResponse)
    async def page(request: Request, page: str):
        if page not in (
            "",
            "orders",
            "sales",
            "rules",
            "merchants",
            "syncs",
            "mcp-guide",
            "agents",
        ):
            raise HTTPException(404)
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", 303)
        return templates.TemplateResponse(
            request=request,
            name="app.html",
            context={
                "page": page or "dashboard",
                "csrf_token": request.session["csrf_token"],
                "username": settings.admin_username,
            },
        )

    return app
