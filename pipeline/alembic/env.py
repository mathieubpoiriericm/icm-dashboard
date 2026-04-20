"""Alembic environment configuration.

Reads database connection parameters from environment variables (same ones
used by the pipeline) and runs migrations using raw SQL.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

# Add project root to path so we can read .env
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# Alembic Config object — provides access to alembic.ini values.
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_database_url() -> str:
    """Build the database URL from environment variables."""
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")

    if not all([name, user, password]):
        missing = [
            k for k, v in [("DB_NAME", name), ("DB_USER", user), ("DB_PASSWORD", password)]
            if not v
        ]
        raise RuntimeError(
            f"Missing required database environment variables: {missing}. "
            f"Set them in .env or as environment variables."
        )

    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    Useful for reviewing migration SQL before applying.
    """
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=None,  # No SQLAlchemy metadata — raw SQL migrations
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Connects to the database and applies migrations directly.
    """
    connectable = create_engine(_get_database_url())

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,  # No SQLAlchemy metadata — raw SQL migrations
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
