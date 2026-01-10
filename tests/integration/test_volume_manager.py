import os
import time

from crader.volume_manager import git_volume_manager as gvm_module


def test_volume_manager_cache_path(tmp_path, monkeypatch):
    monkeypatch.setattr(gvm_module, "STORAGE_ROOT", str(tmp_path))
    manager = gvm_module.GitVolumeManager()
    path = manager._get_repo_cache_path("https://example.com/repo.git")
    assert path.startswith(str(tmp_path))
    assert path.endswith(".git")


def test_cleanup_orphaned_workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(gvm_module, "STORAGE_ROOT", str(tmp_path))
    manager = gvm_module.GitVolumeManager()

    stale_dir = os.path.join(manager.workspaces_dir, "old")
    os.makedirs(stale_dir, exist_ok=True)
    old_time = time.time() - 7200
    os.utime(stale_dir, (old_time, old_time))

    cache_repo = os.path.join(manager.cache_dir, "repo.git")
    os.makedirs(cache_repo, exist_ok=True)

    monkeypatch.setattr(manager, "_run_git", lambda _cwd, _args: None)

    manager.cleanup_orphaned_workspaces(max_age_seconds=3600)
    assert not os.path.exists(stale_dir)
