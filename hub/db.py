import json
import os
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from .locking import DataLock


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
 name TEXT PRIMARY KEY, synced_at TEXT, sync_error TEXT
);
CREATE TABLE IF NOT EXISTS items (
 repo TEXT NOT NULL REFERENCES repositories(name) ON DELETE CASCADE,
 number INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, labels TEXT NOT NULL,
 updated_at TEXT NOT NULL, author TEXT NOT NULL,
 PRIMARY KEY(repo, number)
);
CREATE TABLE IF NOT EXISTS comments (
 repo TEXT NOT NULL, number INTEGER NOT NULL, id INTEGER NOT NULL,
 body TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(repo, number, id),
 FOREIGN KEY(repo,number) REFERENCES items(repo,number) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS drafts (
 repo TEXT NOT NULL, number INTEGER NOT NULL, body TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(repo, number)
);
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL,
 body TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
 comment_id INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version=1;
"""


class Database:
    def __init__(self, data_dir, backup_dir):
        self.data_dir = Path(data_dir)
        self.backup_dir = Path(backup_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "hub.sqlite3"
        self.data_lock = DataLock(self.data_dir)
        self.lock = threading.RLock()
        self.backup_error = None
        self.backup_at = None
        try:
            self._initialize()
        except Exception:
            if hasattr(self, "conn"):
                self.conn.close()
            self.data_lock.close()
            raise

    def _initialize(self):
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        check = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError("DB integrity check failed; stop and restore explicitly.")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError("Unsupported DB version; preserve the DB and use the matching app.")
        self.conn.executescript(SCHEMA)
        # A crash after GitHub accepted a post must not enable automatic retries.
        self.write(lambda c: c.execute(
            "UPDATE submissions SET status='unknown', error=? WHERE status='sending'",
            ("前回の送信中にアプリが停止しました。同期またはGitHubで結果を確認してください。",)
        ))

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, args).fetchall()]

    def write(self, operation):
        with self.lock:
            with self.conn:
                result = operation(self.conn)
            self.backup()
            return result

    def backup(self):
        with self.lock:
            temp = self.backup_dir / "latest.sqlite3.tmp"
            try:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                if temp.exists():
                    temp.unlink()
                target = sqlite3.connect(temp)
                try:
                    self.conn.backup(target)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup integrity check failed")
                finally:
                    target.close()
                with temp.open("rb") as file:
                    os.fsync(file.fileno())
                os.replace(temp, self.backup_dir / "latest.sqlite3")
                self.backup_at = now()
                self.backup_error = None
            except (OSError, sqlite3.Error, RuntimeError):
                self.backup_error = "バックアップ更新に失敗しました。保存先の権限と空き容量を確認してください。"
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def close(self):
        with self.lock:
            self.conn.close()
            self.data_lock.close()

    def setting(self, key, default):
        rows = self.rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default
