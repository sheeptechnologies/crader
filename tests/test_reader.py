import pytest

from code_graph_indexer.reader import CodeReader


class FakeStorage:
    def __init__(self, manifest, content_map):
        self._manifest = manifest
        self._content_map = content_map
        self.calls = []

    def get_snapshot_manifest(self, snapshot_id):
        self.calls.append(("manifest", snapshot_id))
        return self._manifest

    def get_file_content_range(self, snapshot_id, file_path, start_line=None, end_line=None):
        self.calls.append(("content", snapshot_id, file_path, start_line, end_line))
        return self._content_map.get(file_path)


def test_read_file_success_and_cache():
    storage = FakeStorage({}, {"a.py": "print('ok')"})
    reader = CodeReader(storage)

    result = reader.read_file("snap", "a.py")
    assert result["content"] == "print('ok')"
    assert result["start_line"] == 1
    assert result["end_line"] == "EOF"

    reader.read_file("snap", "a.py")
    assert storage.calls.count(("content", "snap", "a.py", None, None)) == 2


def test_read_file_missing_raises():
    storage = FakeStorage({}, {})
    reader = CodeReader(storage)
    with pytest.raises(FileNotFoundError):
        reader.read_file("snap", "missing.py")


def test_list_directory_and_find_directories():
    manifest = {
        "type": "dir",
        "children": {
            "src": {
                "type": "dir",
                "children": {
                    "app.py": {"type": "file"},
                    "utils": {"type": "dir", "children": {}},
                },
            },
            "README.md": {"type": "file"},
        },
    }
    storage = FakeStorage(manifest, {})
    reader = CodeReader(storage)

    root_listing = reader.list_directory("snap")
    assert root_listing[0]["type"] == "dir"
    assert root_listing[-1]["name"] == "README.md"

    src_listing = reader.list_directory("snap", "src")
    assert {item["name"] for item in src_listing} == {"app.py", "utils"}

    with pytest.raises(NotADirectoryError):
        reader.list_directory("snap", "src/app.py")

    matches = reader.find_directories("snap", "ut")
    assert "src/utils" in matches
