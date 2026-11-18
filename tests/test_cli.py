from datetime import date

from sqlalchemy import inspect

from takeaway_insight.cli import initialize
from takeaway_insight.database_migrations import upgrade_database
from takeaway_insight.db import Database
from takeaway_insight.demo import seed_demo
from takeaway_insight.services import InsightService


def test_agent_init_can_enable_existing_task_and_configures_dsh_browser(
    tmp_path, monkeypatch, capsys
):
    import sys

    from takeaway_insight.agent_jobs import AgentJobs
    from takeaway_insight.cli import main
    from takeaway_insight.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAKEAWAY_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setattr(sys, "argv", ["takeaway", "agent-init", "--runner", "dsh"])
    main()
    monkeypatch.setattr(sys, "argv", ["takeaway", "agent-init", "--enable"])
    main()
    db = Database(f"sqlite:///{tmp_path}/cli.db")
    tasks = AgentJobs(db, Settings()).list_tasks()
    assert len(tasks) == 1 and tasks[0]["enabled"]
    assert tasks[0]["runner"] == "dsh"
    assert tasks[0]["arguments"] == ["--patch", str(tmp_path / "config/dsh.browser.yml")]
    capsys.readouterr()
    db.dispose()


def test_initialization_generates_unique_private_secrets_and_preserves_existing_config(tmp_path):
    initialize(tmp_path)
    path = tmp_path / ".env"
    first = path.read_text()
    assert "TAKEAWAY_ADMIN_PASSWORD=" in first
    assert "TAKEAWAY_MCP_TOKEN=" in first
    assert path.stat().st_mode & 0o777 == 0o600
    initialize(tmp_path)
    assert path.read_text() == first


def test_migrations_can_run_twice_and_demo_is_explicit_and_idempotent(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/app.db")
    upgrade_database(db)
    upgrade_database(db)
    assert "alembic_version" in inspect(db.engine).get_table_names()
    service = InsightService(db)
    seed_demo(service, date(2026, 11, 17))
    count = service.get_orders(date(2026, 11, 10), date(2026, 11, 17))["total"]
    assert count > 10
    seed_demo(service, date(2026, 11, 17))
    assert service.get_orders(date(2026, 11, 10), date(2026, 11, 17))["total"] == count
    assert all(m["is_demo"] and not m["enabled"] for m in service.list_merchants())
    db.dispose()
