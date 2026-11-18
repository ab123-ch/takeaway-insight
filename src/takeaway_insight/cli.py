"""本地与服务器使用同一命令入口，不输出凭证。"""

import argparse
import asyncio
import json
import os
import secrets
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from takeaway_insight.config import Settings
from takeaway_insight.database_migrations import upgrade_database
from takeaway_insight.db import Database
from takeaway_insight.services import InsightService


def initialize(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    if path.exists():
        return False
    content = "\n".join(
        [
            "TAKEAWAY_DATABASE_URL=sqlite:///./data/takeaway.db",
            "TAKEAWAY_ADMIN_USERNAME=admin",
            "TAKEAWAY_ADMIN_PASSWORD=" + secrets.token_urlsafe(18),
            "TAKEAWAY_SECRET_KEY=" + secrets.token_urlsafe(48),
            "TAKEAWAY_MCP_TOKEN=" + secrets.token_urlsafe(36),
            "TAKEAWAY_PUBLIC_URL=http://127.0.0.1:8765",
            "TAKEAWAY_SECURE_COOKIES=false",
            "",
            "# 每个门店的授权凭据单独命名，在网页中填写这些变量的名称。",
            "# ELM_SHOP1_APP_KEY=",
            "# ELM_SHOP1_APP_SECRET=",
            "# ELM_SHOP1_ACCESS_TOKEN=",
            "",
        ]
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    return True


def load_runtime():
    # 平台适配器读取 os.environ；不只依赖 Settings 的 TAKEAWAY_ 前缀。
    load_dotenv(Path.cwd() / ".env", override=False)
    return Settings()


def main():
    parser = argparse.ArgumentParser(prog="takeaway", description="餐账：外卖经营分析")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="生成私有配置并迁移数据库；保留已有 .env")
    serve = commands.add_parser("serve", help="启动网页与 MCP HTTP")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    commands.add_parser("worker", help="启动本地Agent定时任务进程")
    commands.add_parser("demo", help="显式导入演示订单；不调用平台")
    commands.add_parser("db-upgrade", help="升级数据库结构")
    agent_init = commands.add_parser("agent-init", help="建立本地Agent采集任务")
    agent_init.add_argument("--runner", choices=["antigravity", "dsh"], default="antigravity")
    agent_init.add_argument("--enable", action="store_true", help="启用每15分钟定时执行")
    agent_run = commands.add_parser("agent-run", help="立即启动一个本地Agent任务")
    agent_run.add_argument("--task", type=int, required=True)
    agent_run.add_argument("--request", default="")
    sync = commands.add_parser("sync", help="同步指定门店与日期")
    sync.add_argument("--merchant", type=int, required=True)
    sync.add_argument("--date", type=date.fromisoformat, required=True)
    sync.add_argument("--end-date", type=date.fromisoformat)
    args = parser.parse_args()
    if args.command == "init":
        created = initialize(Path.cwd())
        print("已生成 .env（权限600）；请在本机查看管理员密码。" if created else "保留已有 .env。")
    settings = load_runtime()
    database = Database(settings.database_url)
    try:
        upgrade_database(database)
        service = InsightService(database)
        if args.command in ("init", "db-upgrade"):
            print("数据库结构已升级。")
        elif args.command == "demo":
            from takeaway_insight.demo import seed_demo

            print(seed_demo(service)["message"])
        elif args.command == "serve":
            import uvicorn

            from takeaway_insight.web import create_app

            uvicorn.run(
                create_app(settings, database),
                host=args.host or settings.host,
                port=args.port or settings.port,
            )
        elif args.command == "worker":
            from takeaway_insight.worker import run_worker

            asyncio.run(run_worker(service, settings))
        elif args.command == "agent-init":
            from takeaway_insight.agent_jobs import AgentJobs, AgentTaskInput

            jobs = AgentJobs(database, settings)
            name = "三平台自动采集"
            existing = next((task for task in jobs.list_tasks() if task["name"] == name), None)
            if existing:
                result = existing
                if args.enable and not existing["enabled"]:
                    values = {
                        key: value
                        for key, value in existing.items()
                        if key not in ("id", "next_run_at")
                    }
                    result = jobs.update_task(
                        existing["id"], AgentTaskInput(**(values | {"enabled": True}))
                    )
            else:
                result = jobs.create_task(
                    AgentTaskInput(
                        name=name,
                        runner=args.runner,
                        enabled=args.enable,
                        interval_minutes=15,
                        arguments=["--patch", str(jobs.project_dir / "config/dsh.browser.yml")]
                        if args.runner == "dsh"
                        else [],
                        variables={"cdp_url": "http://127.0.0.1:9222"},
                    )
                )
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "agent-run":
            from takeaway_insight.agent_jobs import AgentExecutor, AgentJobs

            jobs = AgentJobs(database, settings)
            run = jobs.enqueue(args.task, request=args.request)
            result = asyncio.run(AgentExecutor(jobs).run(run["id"]))
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in ("id", "status", "exit_code", "log_path", "error")
                    },
                    ensure_ascii=False,
                )
            )
            if result["status"] != "completed":
                raise SystemExit(1)
        elif args.command == "sync":
            from takeaway_insight.sync import SyncRunner

            result = asyncio.run(
                SyncRunner(service, settings).sync_range(
                    args.merchant, args.date, args.end_date or args.date
                )
            )
            print(json.dumps(result, ensure_ascii=False))
            if any(row["status"] != "success" for row in result):
                raise SystemExit(1)
    except (ValueError, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            return
        parser.exit(2, f"配置或输入错误：{error}\n")
    finally:
        database.dispose()
