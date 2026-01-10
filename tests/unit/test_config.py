import importlib
import os

import crader.config as config


def test_config_uses_env_path(monkeypatch, tmp_path):
    monkeypatch.setenv("REPO_VOLUME", str(tmp_path))
    module = importlib.reload(config)
    assert module.STORAGE_ROOT == str(tmp_path)


def test_config_handles_makedirs_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REPO_VOLUME", str(tmp_path / "fail"))

    def raise_oserror(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(os, "makedirs", raise_oserror)
    module = importlib.reload(config)
    captured = capsys.readouterr()
    assert "Warning" in captured.out
    assert os.path.isabs(module.STORAGE_ROOT)
