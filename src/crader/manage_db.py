import logging
import os

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI_PATH = os.path.join(BASE_DIR, "db", "alembic.ini")

def get_alembic_config(db_url: str = None):
    """
    Creates an Alembic configuration object pointing to the internal
    alembic.ini and migrations directory.
    """
    if not os.path.exists(ALEMBIC_INI_PATH):
        raise FileNotFoundError(f"Alembic config not found at {ALEMBIC_INI_PATH}")

    alembic_cfg = Config(ALEMBIC_INI_PATH)

    # Point 'script_location' to the internal 'db' folder
    # This overrides the value in alembic.ini (which is 'db') to be absolute
    script_location = os.path.join(BASE_DIR, "db")
    alembic_cfg.set_main_option("script_location", script_location)

    if db_url:
        # Override sqlalchemy.url with the user provided one (env var or arg)
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    return alembic_cfg

def run_upgrade(db_url: str, revision: str = "head"):
    """Run alembic upgrade."""
    logger.info(f"Running DB upgrade to {revision}...")
    config = get_alembic_config(db_url)
    command.upgrade(config, revision)
    logger.info("DB upgrade completed.")
