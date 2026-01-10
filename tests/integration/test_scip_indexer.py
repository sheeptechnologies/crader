from crader.graph.indexers.scip import (
    DiskSymbolTable,
    SCIPIndexer,
    SCIPRunner,
    get_relation_verb,
)


def test_get_relation_verb():
    assert get_relation_verb(1) == "defines"
    assert get_relation_verb(64) == "overrides"
    assert get_relation_verb(128) == "implements"
    assert get_relation_verb(32) == "writes_to"
    assert get_relation_verb(16) == "reads_from"
    assert get_relation_verb(0) == "calls"


def test_disk_symbol_table_add_get(tmp_path):
    table = DiskSymbolTable()
    table.add("symbol", "file.py", [0, 1, 0, 2], is_local=True)
    table.flush()
    result = table.get("symbol", "file.py")
    assert result[0] == "file.py"
    assert result[1] == [0, 1, 0, 2]
    assert table.close() >= 1


def test_scip_runner_filters_and_discovery(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "package.json").write_text("{}")

    runner = SCIPRunner(str(repo))

    monkeypatch.setattr(runner, "PROJECT_MARKERS", {"package.json": "scip-typescript"})
    monkeypatch.setattr(runner, "EXTENSION_MAP", {".ts": "scip-typescript"})
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/scip")

    tasks = runner._discover_tasks()
    assert tasks

    assert runner._should_skip_document("node_modules/a.ts") is True
    assert runner._should_skip_document("src/app.ts") is False


def test_scip_indexer_helpers(tmp_path):
    repo = tmp_path
    file_path = repo / "a.py"
    file_path.write_text("def foo():\n    pass\n")

    indexer = SCIPIndexer(str(repo))

    assert indexer._clean_symbol("scip python .py/foo") == "foo"
    assert indexer._extract_symbol_name("a.py", [0, 4, 0, 7]) == "foo"

    byte_range = indexer._bytes("a.py", [0, 0, 0, 3])
    assert byte_range == [0, 3]
