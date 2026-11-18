import asyncio
import os
import signal
import sys
from datetime import date, timedelta

import pytest

from takeaway_insight.agent_jobs import AgentExecutor, AgentJobs, AgentTaskInput
from takeaway_insight.config import Settings
from takeaway_insight.database_migrations import upgrade_database
from takeaway_insight.db import Database
from takeaway_insight.models import AgentTask, utcnow


@pytest.fixture
def jobs(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/agent.db")
    upgrade_database(db)
    service = AgentJobs(db, Settings(database_url=f"sqlite:///{tmp_path}/agent.db"), tmp_path)
    yield service
    db.dispose()


async def test_native_agent_receives_prompt_and_can_write_local_files(jobs, tmp_path):
    target = tmp_path / "agent-wrote.txt"
    program = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read()); print('已采集并核对报告，普通文本即可')"
    task = jobs.create_task(
        AgentTaskInput(
            name="本地测试",
            runner="custom",
            command=[sys.executable, "-c", program, str(target)],
            prompt="采集 {{ today }}，请求：{{ request }}",
        )
    )
    run = jobs.enqueue(task["id"], trigger="manual", request="今天全部订单")
    result = await AgentExecutor(jobs).run(run["id"])
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert "今天全部订单" in target.read_text()
    assert "普通文本即可" in jobs.read_log(run["id"])


async def test_failed_agent_is_recorded(jobs):
    task = jobs.create_task(
        AgentTaskInput(
            name="失败",
            runner="custom",
            command=[sys.executable, "-c", "print('具体错误');raise SystemExit(7)"],
            prompt="任务",
        )
    )
    first = jobs.enqueue(task["id"], request="采集")
    result = await AgentExecutor(jobs).run(first["id"])
    assert result["status"] == "failed" and result["exit_code"] == 7
    assert "具体错误" in jobs.read_log(first["id"])


async def test_stopping_executor_terminates_agent_and_records_interruption(jobs):
    task = jobs.create_task(
        AgentTaskInput(
            name="中断",
            runner="custom",
            command=[sys.executable, "-c", "import time;time.sleep(60)"],
            prompt="任务",
        )
    )
    run = jobs.enqueue(task["id"])
    execution = asyncio.create_task(AgentExecutor(jobs).run(run["id"]))
    try:
        for _ in range(200):
            result = jobs.get_run(run["id"])
            if result["pid"]:
                break
            await asyncio.sleep(0.01)
        assert result["pid"]
    finally:
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    final = jobs.get_run(run["id"])
    assert final["status"] == "interrupted" and final["finished_at"]
    with pytest.raises(ProcessLookupError):
        os.kill(result["pid"], 0)


@pytest.mark.skipif(os.name == "nt", reason="验证POSIX/systemd的SIGTERM停止流程")
async def test_worker_sigterm_stops_agent_and_records_interruption(jobs):
    jobs.create_task(
        AgentTaskInput(
            name="服务停止",
            runner="custom",
            command=[sys.executable, "-c", "import time;time.sleep(60)"],
            enabled=True,
            interval_minutes=15,
        )
    )
    program = """
import asyncio,sys
from takeaway_insight.agent_jobs import AgentJobs,run_agent_worker
from takeaway_insight.db import Database
from takeaway_insight.config import Settings
db=Database(sys.argv[1])
try:
    asyncio.run(run_agent_worker(AgentJobs(db,Settings(),sys.argv[2])))
finally:
    db.dispose()
"""
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        str(jobs.db.engine.url),
        str(jobs.project_dir),
        start_new_session=True,
    )
    agent_pid = None
    try:
        for _ in range(300):
            runs = jobs.list_runs()
            if runs and runs[0]["pid"]:
                agent_pid = runs[0]["pid"]
                break
            await asyncio.sleep(0.01)
        assert agent_pid
        worker.terminate()
        await asyncio.wait_for(worker.wait(), 10)
        result = jobs.list_runs()[0]
        assert result["status"] == "interrupted" and result["finished_at"]
        with pytest.raises(ProcessLookupError):
            os.kill(agent_pid, 0)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()
        if agent_pid:
            try:
                os.killpg(agent_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


async def test_log_directory_failure_finishes_run(jobs, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("file")
    jobs.log_dir = blocked / "logs"
    task = jobs.create_task(
        AgentTaskInput(
            name="日志失败", runner="custom", command=[sys.executable, "-c", "print('ok')"]
        )
    )
    run = jobs.enqueue(task["id"])
    result = await AgentExecutor(jobs).run(run["id"])
    assert result["status"] == "failed" and result["finished_at"]
    assert "NotADirectoryError" in result["error"]


def test_invalid_template_does_not_block_other_scheduled_tasks(jobs):
    for name, prompt in (("错误模板", "采集{{ typo_name }}"), ("正常模板", "采集")):
        jobs.create_task(
            AgentTaskInput(
                name=name,
                runner="custom",
                command=[sys.executable, "-c", "print('ok')"],
                prompt=prompt,
                enabled=True,
                interval_minutes=15,
            )
        )
    assert jobs.enqueue_due() == 2
    runs = jobs.list_runs()
    assert {run["status"] for run in runs} == {"queued", "failed"}
    failure = next(run for run in runs if run["status"] == "failed")
    assert "typo_name" in jobs.read_log(failure["id"])
    assert jobs.enqueue_due() == 0


async def test_two_executors_do_not_start_the_same_run_twice(jobs, tmp_path):
    target = tmp_path / "count.txt"
    task = jobs.create_task(
        AgentTaskInput(
            name="单次",
            runner="custom",
            command=[
                sys.executable,
                "-c",
                "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);p.write_text('once');time.sleep(.1)",
                str(target),
            ],
            prompt="执行",
        )
    )
    run = jobs.enqueue(task["id"])
    await asyncio.gather(AgentExecutor(jobs).run(run["id"]), AgentExecutor(jobs).run(run["id"]))
    assert target.read_text() == "once"
    assert jobs.get_run(run["id"])["status"] == "completed"


def test_due_schedule_uses_same_agent_queue(jobs):
    task = jobs.create_task(
        AgentTaskInput(
            name="定时",
            runner="custom",
            command=[sys.executable, "-c", "print('ok')"],
            prompt="采集",
            enabled=True,
            interval_minutes=15,
        )
    )
    now = utcnow()
    with jobs.db.session() as session:
        session.get(AgentTask, task["id"]).next_run_at = now - timedelta(seconds=1)
        session.commit()
    assert jobs.enqueue_due(now) == 1
    assert jobs.enqueue_due(now) == 0
    assert jobs.list_runs()[0]["trigger"] == "schedule"


def test_full_access_presets_and_freeform_prompt(jobs):
    for runner in ("antigravity", "dsh"):
        task = jobs.create_task(
            AgentTaskInput(name=runner, runner=runner, prompt="自由任务，不要求JSON {{ request }}")
        )
        run = jobs.enqueue(task["id"], request="自行调用数据库技能")
        assert "自行调用数据库技能" in run["prompt"]
        assert not any("timeout" in arg for arg in run["command"])
        if runner == "antigravity":
            assert "--sandbox=false" in run["command"]
            assert "--dangerously-skip-permissions" in run["command"]
        else:
            assert "danger-full-access" in run["command"]


async def test_worker_automatically_starts_due_local_agent(jobs):
    from takeaway_insight.agent_jobs import run_agent_worker

    jobs.create_task(
        AgentTaskInput(
            name="自动",
            runner="custom",
            command=[sys.executable, "-c", "print('自动完成')"],
            prompt="自动执行",
            enabled=True,
            interval_minutes=15,
        )
    )
    worker = asyncio.create_task(run_agent_worker(jobs))
    try:
        for _ in range(200):
            rows = jobs.list_runs()
            if rows and rows[0]["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert rows and rows[0]["status"] == "completed"
        assert "自动完成" in jobs.read_log(rows[0]["id"])
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker


async def test_agent_process_uses_database_skill_to_insert_and_reconcile(jobs):
    program = """
import json
from datetime import date
from takeaway_insight.cli import load_runtime
from takeaway_insight.db import Database
from takeaway_insight.database_skill import DatabaseSkill
from takeaway_insight.services import InsightService
db=Database(load_runtime().database_url)
service=InsightService(db)
merchant=service.create_merchant(name="实链路测试店",platform="meituan",external_shop_id="native-test")
rows=[{"external_id":"order-e2e","placed_at":"2026-11-18T12:00:00+08:00","status":"completed","total_amount":"76","items":[{"external_id":"1","name":"半只鸡+半只鸭","quantity":"2"}]}]
skill=DatabaseSkill(db)
print("入库",skill.ingest(merchant["id"],rows))
report=skill.reconcile(merchant["id"],date(2026,11,18),rows)
print("整体核对",json.dumps(report,ensure_ascii=False))
db.dispose()
raise SystemExit(0 if report["ok"] else 1)
"""
    task = jobs.create_task(
        AgentTaskInput(
            name="数据库技能全链路",
            runner="custom",
            command=[sys.executable, "-c", program],
            prompt="读取真实数据，调用技能入库，再核对",
        )
    )
    run = jobs.enqueue(task["id"])
    result = await AgentExecutor(jobs).run(run["id"])
    assert result["status"] == "completed"
    assert '"ok": true' in jobs.read_log(run["id"])
    from takeaway_insight.services import InsightService

    report = InsightService(jobs.db).get_sales_summary(date(2026, 11, 18), date(2026, 11, 18))
    assert report["summary"]["order_count"] == 1
    assert report["summary"]["item_quantity"] == "4"
