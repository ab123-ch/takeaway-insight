from alembic import command
from alembic.config import Config

from takeaway_insight.config import PACKAGE_DIR


def upgrade_database(database):
    config = Config()
    config.set_main_option("script_location", str(PACKAGE_DIR / "migrations"))
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
