"""Explicit offline restore: python -m hub.restore --backup backups/latest.sqlite3."""
import argparse
import os
from pathlib import Path
import sqlite3
import uuid

from .locking import DataLock


def restore(backup, data_dir, replace=False):
    backup, data_dir = Path(backup).resolve(), Path(data_dir).resolve()
    if not backup.is_file():
        raise RuntimeError("Backup file does not exist.")
    lock = DataLock(data_dir)
    target = data_dir / "hub.sqlite3"
    temp = data_dir / ("restore-" + str(uuid.uuid4()) + ".sqlite3")
    try:
        if backup == target:
            raise RuntimeError("Backup and destination must be different files.")
        if target.exists() and not replace:
            raise RuntimeError("DB already exists. Preserve it, then use --replace for an explicit replacement.")
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(target) + suffix).exists():
                raise RuntimeError("SQLite recovery files exist. Preserve all DB files and recover them before restoring.")
        source = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
        try:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed.")
            if source.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("Unsupported backup schema.")
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"repositories", "items", "comments", "drafts", "submissions", "settings"}.issubset(tables):
                raise RuntimeError("Backup is not a GitHub Local Hub DB.")
            dest = sqlite3.connect(temp)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        with temp.open("rb") as file:
            os.fsync(file.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="Restore a checked SQLite backup while the app is stopped.")
    parser.add_argument("--backup", default="backups/latest.sqlite3")
    parser.add_argument("--data", default="data")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        restore(args.backup, args.data, args.replace)
    except (RuntimeError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"Restore failed: {error}\n")
    print("Restored. Start the app and verify repositories, drafts, and submission history.")


if __name__ == "__main__":
    main()
