"""原生Agent启动器：传递提示词、执行命令、记录进程结果，不代理采集或入库。"""

import asyncio
import json
import logging
import os
import signal
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from takeaway_insight.config import PACKAGE_DIR, Settings
from takeaway_insight.models import AgentRun, AgentTask, utcnow

DEFAULT_PROMPT = (PACKAGE_DIR / "prompts/autonomous-collection.md").read_text()
FULL_ACCESS_SETTINGS = {
    "agentMode": "",
    "toolPermission": "always-proceed",
    "artifactReviewPolicy": "always-proceed",
    "enableTerminalSandbox": False,
    "allowNonWorkspaceAccess": True,
}
logger = logging.getLogger(__name__)


class AgentTaskInput(BaseModel):
    name: str = Field(min_length=1)
    runner: Literal["antigravity", "dsh", "custom"] = "antigravity"
    command: list[str] = Field(default_factory=list)
    arguments: list[str] = Field(default_factory=list)
    prompt: str = Field(default=DEFAULT_PROMPT, min_length=1)
    variables: dict = Field(default_factory=dict)
    enabled: bool = False
    interval_minutes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_task(self):
        if self.runner == "custom" and (not self.command or not self.command[0]):
            raise ValueError("自定义Agent需要填写实际命令")
        if self.enabled and self.interval_minutes is None:
            raise ValueError("启用定时需要填写执行间隔")
        return self


class AgentJobs:
    def __init__(self, database, settings: Settings, project_dir=None):
        self.db, self.settings = database, settings
        self.project_dir = Path(project_dir or settings.agent_project_dir or Path.cwd()).resolve()
        self.log_dir = Path(settings.agent_log_dir)
        if not self.log_dir.is_absolute():
            self.log_dir = self.project_dir / self.log_dir

    @staticmethod
    def serialize_task(task):
        return {
            key: getattr(task, key)
            for key in (
                "id",
                "name",
                "runner",
                "command",
                "arguments",
                "prompt",
                "variables",
                "enabled",
                "interval_minutes",
            )
        } | {"next_run_at": task.next_run_at.isoformat() if task.next_run_at else None}

    def list_tasks(self):
        with self.db.session() as session:
            return [
                self.serialize_task(task)
                for task in session.scalars(select(AgentTask).order_by(AgentTask.id))
            ]

    def create_task(self, data: AgentTaskInput):
        with self.db.session() as session:
            task = AgentTask(**data.model_dump())
            session.add(task)
            session.commit()
            return self.serialize_task(task)

    def update_task(self, task_id, data: AgentTaskInput):
        with self.db.session() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                raise ValueError("Agent任务不存在")
            for key, value in data.model_dump().items():
                setattr(task, key, value)
            task.next_run_at = None
            session.commit()
            return self.serialize_task(task)

    def _command(self, task, prompt):
        if task.runner == "custom":
            return [
                argument.replace("{prompt}", prompt) for argument in task.command + task.arguments
            ]
        if task.runner == "dsh":
            return [
                self.settings.dsh_executable,
                "--profile",
                "headless",
                "--permission-mode",
                "danger-full-access",
                "--approval-policy",
                "never",
                *task.arguments,
                "-",
            ]
        return [
            self.settings.antigravity_executable,
            "--settings",
            json.dumps(FULL_ACCESS_SETTINGS),
            "--sandbox=false",
            "--dangerously-skip-permissions",
            *task.arguments,
            "-p",
            prompt,
        ]

    def _enqueue(self, session, task, trigger, request):
        run_id = uuid4().hex
        now = utcnow().astimezone(ZoneInfo(self.settings.timezone))
        context = {
            "project_dir": str(self.project_dir),
            "database_skill": str(PACKAGE_DIR / "skills/takeaway-database/SKILL.md"),
            "today": now.date().isoformat(),
            "now": now.isoformat(),
            "run_id": run_id,
            "task_name": task.name,
            "trigger": trigger,
            "request": request,
            "variables": task.variables,
        }
        error = ""
        try:
            prompt = (
                Environment(undefined=StrictUndefined).from_string(task.prompt).render(**context)
            )
        except TemplateError as failure:
            prompt = task.prompt
            error = f"任务 {task.name} 提示词渲染失败：{failure}"
        if request:
            prompt += "\n\n本次触发附加要求：\n" + request
        run = AgentRun(
            id=run_id,
            task_id=task.id,
            trigger=trigger,
            request=request,
            runner=task.runner,
            command=self._command(task, prompt) if not error else [],
            prompt=prompt,
            status="failed" if error else "queued",
            error=error,
            finished_at=utcnow() if error else None,
            log_path=str(self.log_dir / f"{run_id}.log"),
        )
        session.add(run)
        session.flush()
        return run.id

    def enqueue(self, task_id, trigger="manual", request=""):
        with self.db.session() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                raise ValueError("Agent任务不存在")
            run_id = self._enqueue(session, task, trigger, request)
            session.commit()
        return self.get_run(run_id)

    def enqueue_due(self, now=None):
        now = now or utcnow()
        count = 0
        with self.db.session() as session:
            tasks = list(
                session.scalars(
                    select(AgentTask).where(
                        AgentTask.enabled.is_(True),
                        or_(AgentTask.next_run_at.is_(None), AgentTask.next_run_at <= now),
                    )
                )
            )
            for task in tasks:
                claimed = session.execute(
                    update(AgentTask)
                    .where(
                        AgentTask.id == task.id,
                        AgentTask.enabled.is_(True),
                        or_(AgentTask.next_run_at.is_(None), AgentTask.next_run_at <= now),
                    )
                    .values(next_run_at=now + timedelta(minutes=task.interval_minutes))
                )
                if claimed.rowcount:
                    self._enqueue(session, task, "schedule", "")
                    count += 1
            session.commit()
        return count

    @staticmethod
    def _serialize_run(run, include_prompt=False):
        data = {
            key: getattr(run, key)
            for key in (
                "id",
                "task_id",
                "trigger",
                "runner",
                "status",
                "pid",
                "exit_code",
                "error",
                "log_path",
            )
        }
        data["task_name"] = run.task.name
        for key in ("created_at", "started_at", "finished_at"):
            value = getattr(run, key)
            data[key] = value.isoformat() if value else None
        if include_prompt:
            data.update(prompt=run.prompt, command=run.command, request=run.request)
        return data

    def get_run(self, run_id):
        with self.db.session() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise ValueError("Agent运行记录不存在")
            return self._serialize_run(run, True)

    def list_runs(self):
        with self.db.session() as session:
            return [
                self._serialize_run(run)
                for run in session.scalars(
                    select(AgentRun)
                    .options(selectinload(AgentRun.task))
                    .order_by(AgentRun.created_at.desc())
                )
            ]

    def read_log(self, run_id):
        run = self.get_run(run_id)
        path = Path(run["log_path"])
        return (
            path.read_text(errors="replace")
            if path.is_file()
            else (run["error"] or "Agent尚未启动，等待worker执行。")
        )

    def queued_ids(self):
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(AgentRun.id)
                    .where(AgentRun.status == "queued")
                    .order_by(AgentRun.created_at)
                )
            )


class AgentExecutor:
    def __init__(self, jobs: AgentJobs):
        self.jobs = jobs

    async def run(self, run_id):
        with self.jobs.db.session() as session:
            result = session.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.status == "queued")
                .values(status="running", started_at=utcnow())
            )
            session.commit()
            if not result.rowcount:
                return self.jobs.get_run(run_id)
        run = self.jobs.get_run(run_id)
        path = Path(run["log_path"])
        process = None
        status, exit_code, error = "failed", None, ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            url = self.jobs.db.engine.url
            if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
                url = url.set(database=str(Path(url.database).resolve()))
            environment["TAKEAWAY_DATABASE_URL"] = url.render_as_string(hide_password=False)
            environment["TAKEAWAY_AGENT_RUN_ID"] = run_id
            with path.open("wb") as log:
                path.chmod(0o600)
                process = await asyncio.create_subprocess_exec(
                    *run["command"],
                    cwd=self.jobs.project_dir,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
                with self.jobs.db.session() as session:
                    session.get(AgentRun, run_id).pid = process.pid
                    session.commit()
                await process.communicate(run["prompt"].encode())
                exit_code = process.returncode
                status = "completed" if exit_code == 0 else "failed"
                if exit_code:
                    error = f"Agent进程退出码：{exit_code}，请查看运行日志"
        except asyncio.CancelledError:
            status, error = "interrupted", "启动器停止，Agent进程已中断，请查看日志与数据库实际状态"
            raise
        except OSError as failure:
            error = f"Agent启动失败：{type(failure).__name__}；请检查命令、安装和工程目录"
        except Exception as failure:
            error = f"Agent执行器异常：{type(failure).__name__}；请检查worker日志与数据库实际状态"
            logger.exception("Agent运行异常：%s", run_id)
        finally:
            if process is not None and process.returncode is None:
                self._terminate(process, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    self._terminate(process, signal.SIGKILL)
                    await process.wait()
            with self.jobs.db.session() as session:
                row = session.get(AgentRun, run_id)
                row.status, row.exit_code, row.error = status, exit_code, error
                row.finished_at = utcnow()
                session.commit()
        return self.jobs.get_run(run_id)

    @staticmethod
    def _terminate(process, sig):
        try:
            if os.name != "nt":
                os.killpg(process.pid, sig)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass  # 进程可能在状态检查与发信号之间自然退出。


async def run_agent_worker(jobs: AgentJobs):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    active = {}
    executor = AgentExecutor(jobs)

    def finished(done, key):
        active.pop(key, None)
        if not done.cancelled() and (failure := done.exception()):
            logger.error("Agent运行收尾失败：%s", key, exc_info=failure)

    async def tick():
        await asyncio.to_thread(jobs.enqueue_due)
        for run_id in await asyncio.to_thread(jobs.queued_ids):
            if run_id not in active:
                task = asyncio.create_task(executor.run(run_id))
                active[run_id] = task
                task.add_done_callback(lambda done, key=run_id: finished(done, key))

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        tick, "interval", seconds=5, next_run_time=utcnow(), max_instances=1, coalesce=True
    )
    scheduler.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    if os.name != "nt":
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        tasks = list(active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if os.name != "nt":
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGTERM, previous_sigterm)
