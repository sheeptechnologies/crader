import os
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from crader.__main__ import cli

def test_index_missing_db_url():
    runner = CliRunner()
    result = runner.invoke(cli, ["index", "http://github.com/foo/bar"])
    assert result.exit_code != 0
    assert "Error: --db-url arg or CRADER_DB_URL" in result.output

@patch("crader.__main__.CodebaseIndexer")
def test_index_success(mock_indexer_cls):
    mock_indexer = mock_indexer_cls.return_value
    mock_indexer.index.return_value = "snap-123"
    
    runner = CliRunner()
    result = runner.invoke(cli, ["index", "http://github.com/foo/bar", "--db-url", "sqlite:///"])
    
    assert result.exit_code == 0
    assert "Indexing completed. Snapshot ID: snap-123" in result.output
    mock_indexer.close.assert_called_once()

def test_db_upgrade_missing_db_url():
    runner = CliRunner()
    result = runner.invoke(cli, ["db", "upgrade"])
    assert result.exit_code != 0
    assert "Error: --db-url arg or CRADER_DB_URL" in result.output

@patch("crader.manage_db.run_upgrade")
def test_db_upgrade_success(mock_run_upgrade):
    runner = CliRunner()
    result = runner.invoke(cli, ["db", "upgrade", "--db-url", "sqlite:///"])
    
    assert result.exit_code == 0
    assert "Database upgraded successfully" in result.output
    mock_run_upgrade.assert_called_once_with("sqlite:///")

@patch("crader.manage_db.run_upgrade")
def test_db_upgrade_failure(mock_run_upgrade):
    mock_run_upgrade.side_effect = Exception("Boom")
    
    runner = CliRunner()
    result = runner.invoke(cli, ["db", "upgrade", "--db-url", "sqlite:///"])
    
    assert result.exit_code == 1
    assert "Database upgrade failed: Boom" in result.output
