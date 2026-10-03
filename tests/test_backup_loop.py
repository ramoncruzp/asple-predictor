from datetime import datetime, timezone
from types import SimpleNamespace

from database.db_manager import DBManager
from scheduler.backup_loop import BackupLoop
import scheduler.backup_loop as backup_module


def test_sqlite_file_pragmas_are_applied_but_memory_keeps_default(tmp_path):
    file_db = DBManager(f"sqlite:///{tmp_path / 'source.db'}")
    with file_db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0
    memory_db = DBManager("sqlite:///:memory:")
    with memory_db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "memory"


def test_backup_contains_database_rows_and_retains_only_newest(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'source.db'}")
    db.seed_coin_if_missing("XRPUSDT", "test")
    directory = tmp_path / "backups"
    settings = SimpleNamespace(db_backup_enabled=True, db_backup_dir=str(directory), db_backup_keep=2)
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    loop = BackupLoop(db, settings, clock=lambda: now)
    first = loop.backup_if_due()
    assert first and first.exists()
    as_backup = DBManager(f"sqlite:///{first}")
    assert as_backup.get_coin("XRPUSDT") is not None
    as_backup.engine.dispose()
    for index in range(2):
        path = directory / f"asple_2026100{index + 1}_000000.db"
        path.write_bytes(b"old backup")
    loop.backup_if_due(force=True)
    assert len(list(directory.glob("asple_*.db"))) == 2


def test_backup_disabled_is_noop(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'source.db'}")
    settings = SimpleNamespace(db_backup_enabled=False, db_backup_dir=str(tmp_path / "backups"),
                               db_backup_keep=7)
    assert BackupLoop(db, settings).backup_if_due() is None
    assert not (tmp_path / "backups").exists()


def test_relative_backup_directory_resolves_from_repository_root(tmp_path, monkeypatch):
    db = DBManager(f"sqlite:///{tmp_path / 'source.db'}")
    monkeypatch.setattr(backup_module, "REPOSITORY_ROOT", tmp_path)
    settings = SimpleNamespace(db_backup_enabled=True, db_backup_dir="relative-backups", db_backup_keep=2)
    loop = BackupLoop(db, settings, clock=lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    result = loop.backup_if_due()
    assert result is not None and result.parent == tmp_path / "relative-backups"


def test_corrupt_backup_is_removed_without_retention_deleting_older_files(tmp_path, monkeypatch):
    db = DBManager(f"sqlite:///{tmp_path / 'source.db'}")
    directory = tmp_path / "backups"
    directory.mkdir()
    older = directory / "asple_20261001_000000.db"
    older.write_bytes(b"older good copy")
    settings = SimpleNamespace(db_backup_enabled=True, db_backup_dir=str(directory), db_backup_keep=1)
    loop = BackupLoop(db, settings, clock=lambda: datetime(2026, 10, 3, 12, tzinfo=timezone.utc))

    class FakeTarget:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql): return SimpleNamespace(fetchone=lambda: ("malformed database",))

    class BrokenConnection:
        driver_connection = SimpleNamespace(backup=lambda _target: None)
        def close(self): pass

    monkeypatch.setattr(db.engine, "raw_connection", lambda: BrokenConnection())
    monkeypatch.setattr(backup_module.sqlite3, "connect", lambda *_args, **_kwargs: FakeTarget())
    result = loop.backup_if_due(force=True)
    assert result is None
    assert older.exists()
    assert sorted(path.name for path in directory.glob("asple_*.db")) == [older.name]
