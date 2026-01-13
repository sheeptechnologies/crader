import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- DYNAMIC URL CONFIGURATION ---
# 1. Check if the URL has already been injected by manage_db.py or alembic.ini
config = context.config
current_url = config.get_main_option("sqlalchemy.url")

# 2. If not, read the URL from the environment or use the default
if not current_url:
    DEFAULT_DEV_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"
    db_url = os.getenv("CRADER_DB_URL", DEFAULT_DEV_URL)
    config.set_main_option("sqlalchemy.url", db_url)

# 3. Driver Validation
final_url = config.get_main_option("sqlalchemy.url")
if final_url and final_url.startswith("postgresql://") and "psycopg" not in final_url:
     # Force psycopg (v3) instead of default psycopg2
     final_url = final_url.replace("postgresql://", "postgresql+psycopg://")
     config.set_main_option("sqlalchemy.url", final_url)

# Setup Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata (Optional in your case, since you use Raw SQL, but good to leave as None)
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    # Create the engine using the dynamically injected configuration
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
