import hashlib

from code_graph_indexer.providers.metadata import GitMetadataProvider, LocalMetadataProvider


class FakeGit:
    def __init__(self, url=None, commit="abc", branch="main"):
        self._url = url
        self._commit = commit
        self._branch = branch

    def get_remote_url(self):
        return self._url

    def get_current_commit(self):
        return self._commit

    def get_current_branch(self):
        return self._branch

    def get_changed_files(self, since_commit):
        return ["a.py"] if since_commit else []


def test_git_metadata_provider_with_remote(tmp_path, monkeypatch):
    provider = GitMetadataProvider(str(tmp_path))
    provider.git = FakeGit(url="https://user:pass@example.com/org/repo.git")

    info = provider.get_repo_info()
    assert info["url"] == "https://example.com/org/repo.git"
    assert info["name"] == "repo"
    assert info["commit_hash"] == "abc"
    assert provider.get_changed_files("deadbeef") == ["a.py"]


def test_git_metadata_provider_local_repo(tmp_path):
    provider = GitMetadataProvider(str(tmp_path))
    provider.git = FakeGit(url=None)

    info = provider.get_repo_info()
    expected_hash = hashlib.md5(str(tmp_path).encode("utf-8")).hexdigest()
    assert info["repo_id"] == expected_hash
    assert info["url"].startswith("local://")


def test_local_metadata_provider(tmp_path):
    provider = LocalMetadataProvider(str(tmp_path))
    info = provider.get_repo_info()
    assert info["repo_id"]
    assert info["commit_hash"] == "local"
    assert provider.get_changed_files("any") == []


def test_local_metadata_provider_unique_ids(tmp_path):
    provider_a = LocalMetadataProvider(str(tmp_path / "a"))
    provider_b = LocalMetadataProvider(str(tmp_path / "b"))
    assert provider_a.get_repo_info()["repo_id"] != provider_b.get_repo_info()["repo_id"]
