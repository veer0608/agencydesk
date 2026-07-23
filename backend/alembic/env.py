"""Alembic environment.

Migrations always run as the *owner* role. The application role has no DDL
rights at all, which is deliberate: schema shape and the RLS policies that
protect it can only be changed through a reviewed migration.
"""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

OWNER_URL = os.environ.get(
    "OWNER_DATABASE_URL",
    "postgresql+psycopg://agencydesk_owner:owner_pw@localhost:5433/agencydesk",
)
config.set_main_option("sqlalchemy.url", OWNER_URL)


def run_migrations_offline() -> None:
    context.configure(url=OWNER_URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
