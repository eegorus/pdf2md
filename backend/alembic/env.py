import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# добавить backend/ в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.engine import Base
from database.models import User, Document, UserApiKey, RefreshToken  # noqa: F401

config = context.config

# DATABASE_URL из переменной окружения (синхронный драйвер для alembic)
db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://prms:prms@postgres:5432/prms")
# alembic использует синхронный psycopg2/psycopg
db_url_sync = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
config.set_main_option("sqlalchemy.url", db_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
