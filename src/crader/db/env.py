import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- CONFIGURAZIONE DINAMICA URL ---
# Leggiamo l'URL dall'ambiente.
# Se non c'è, usiamo un default locale per sviluppo (comodo, ma sicuro perché locale)
DEFAULT_DEV_URL = "postgresql://sheep_user:sheep_password@localhost:5433/sheep_index"
db_url = os.getenv("CRADER_DB_URL", DEFAULT_DEV_URL)

# Forza l'uso di psycopg2 se non specificato (per compatibilità SQLAlchemy)
if db_url.startswith("postgresql://") and "psycopg" not in db_url:
    # SQLAlchemy usa psycopg2 di default per postgresql://, quindi è ok.
    pass

# Aggiorniamo la configurazione di Alembic con l'URL dinamico
config = context.config
config.set_main_option("sqlalchemy.url", db_url)

# Setup Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata (Opzionale nel tuo caso, dato che usi Raw SQL, ma utile lasciare a None)
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
    # Creiamo l'engine usando la configurazione iniettata dinamicamente
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
