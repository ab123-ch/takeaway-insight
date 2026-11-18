"""SQLAlchemy 会话管理。"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from takeaway_insight.models import Base


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        options = {"pool_pre_ping": True}
        if parsed.drivername.startswith("sqlite"):
            if parsed.database and parsed.database != ":memory:":
                Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if not parsed.database or parsed.database == ":memory:":
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_options(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
