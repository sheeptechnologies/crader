import os
from unittest.mock import MagicMock, patch

import pytest
from crader.manage_db import get_alembic_config, run_upgrade, ALEMBIC_INI_PATH

def test_get_alembic_config_success(monkeypatch):
    # Ensure alembic.ini exists (it should in dev, but let's mock exists if needed)
    monkeypatch.setattr(os.path, "exists", lambda p: p == ALEMBIC_INI_PATH or True)
    
    config = get_alembic_config(db_url="postgresql://test:test@localhost/db")
    
    assert config.get_main_option("sqlalchemy.url") == "postgresql://test:test@localhost/db"
    assert config.get_main_option("script_location").endswith("src/crader/db")

def test_get_alembic_config_file_not_found(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    
    with pytest.raises(FileNotFoundError):
        get_alembic_config("sqlite:///")

@patch("crader.manage_db.command.upgrade")
@patch("crader.manage_db.get_alembic_config")
def test_run_upgrade(mock_get_config, mock_upgrade):
    mock_config_obj = MagicMock()
    mock_get_config.return_value = mock_config_obj
    
    run_upgrade("sqlite:///", "head")
    
    mock_get_config.assert_called_once_with("sqlite:///")
    mock_upgrade.assert_called_once_with(mock_config_obj, "head")
