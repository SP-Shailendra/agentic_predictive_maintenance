from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection


BASELINE_REVISION = "20261007_0001"


def upgrade_database(connection: Connection) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection

    schema = inspect(connection)
    if not schema.has_table("alembic_version"):
        required_tables = {"plants", "assets", "sensor_data", "work_orders", "workflow_events", "knowledge_chunks"}
        existing_tables = required_tables.intersection(schema.get_table_names())
        if existing_tables == required_tables:
            command.stamp(config, BASELINE_REVISION)
        elif existing_tables:
            raise RuntimeError(
                "Partial legacy schema detected. Back up the database and baseline it manually before upgrading."
            )
    command.upgrade(config, "head")