import subprocess

from crader.utils.git import GitClient
from crader.utils.hashing import compute_file_hash


def test_compute_file_hash_is_deterministic():
    data = b"hello"
    assert compute_file_hash(data) == compute_file_hash(data)
    assert compute_file_hash(b"hello!") != compute_file_hash(data)


def test_git_client_run_git_handles_error(monkeypatch, tmp_path):
    client = GitClient(str(tmp_path))

    def raise_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(subprocess, "check_output", raise_error)
    assert client._run_git(["rev-parse", "HEAD"]) == ""


def test_git_client_helpers(monkeypatch, tmp_path):
    client = GitClient(str(tmp_path))

    def fake_check_output(args, cwd, text, stderr):
        if args[1:] == ["config", "--get", "remote.origin.url"]:
            return "https://example.com/repo.git\n"
        if args[1:] == ["rev-parse", "HEAD"]:
            return "abc123\n"
        if args[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main\n"
        if args[1:] == ["diff", "--name-only", "deadbeef", "HEAD"]:
            return "a.py\n\n"
        return ""

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    assert client.get_remote_url() == "https://example.com/repo.git"
    assert client.get_current_commit() == "abc123"
    assert client.get_current_branch() == "main"
    assert client.get_changed_files("deadbeef") == ["a.py"]


def test_git_client_changed_files_invalid(monkeypatch, tmp_path):
    client = GitClient(str(tmp_path))

    def raise_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(subprocess, "check_output", raise_error)
    assert client.get_changed_files("") == []
    assert client.get_changed_files("unknown") == []
    assert client.get_changed_files("deadbeef") == []
