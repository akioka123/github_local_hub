import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from hub.db import Database
from hub.github import GitHub, GitHubError
from hub.restore import restore
from hub.server import make_server
from hub.service import Hub, HubError


def fixture():
    return [
        {"number": 1, "kind": "issue", "state": "open", "title": "同期の状況を確認する",
         "body": "# Issue\n本文 <script>alert(1)</script>", "labels": ["status:ready", "custom:team"],
         "updated_at": "2026-10-04T10:00:00Z", "author": "owner", "comments": []},
        {"number": 2, "kind": "pr", "state": "merged", "title": "バックアップを改善する",
         "body": "PR body", "labels": ["type:infra"], "updated_at": "2026-10-04T11:00:00Z", "author": "dev",
         "comments": [{"id": 10, "body": "確認しました", "user": {"login": "reviewer"}, "created_at": "2026-10-04T11:00:00Z"}]},
    ]


class FakeGitHub:
    token = "test-token-never-return-this"

    def __init__(self):
        self.data = fixture()
        self.error = None
        self.post_error = None
        self.posts = []

    def snapshot(self, repo):
        if self.error:
            raise self.error
        return copy.deepcopy(self.data)

    def post_comment(self, repo, number, body):
        self.posts.append((repo, number, body))
        result = {"id": 99, "body": body, "user": {"login": "owner"}, "created_at": "2026-10-05T00:00:00Z"}
        if not self.post_error or self.post_error.uncertain:
            next(item for item in self.data if item["number"] == number)["comments"].append(result)
        if self.post_error:
            raise self.post_error
        return result


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "data", self.root / "backups")
        self.github = FakeGitHub()
        self.hub = Hub(self.db, self.github)
        self.hub.add_repository("Owner/Repo")
        self.hub.sync("owner/repo")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_filters_and_custom_labels(self):
        rows = self.hub.list_items({"labels": ["custom:team", "missing"]})
        self.assertEqual([row["number"] for row in rows], [1])
        rows = self.hub.list_items({"kind": "pr", "state": "merged"})
        self.assertEqual([row["number"] for row in rows], [2])
        self.assertEqual(self.hub.list_items({"kind": "issue", "state": "merged"}), [])
        self.assertIn("custom:team", self.hub.labels())
        filters = {"repo": "Owner/Repo", "kind": "issue", "state": "open", "labels": ["custom:team"]}
        self.hub.save_filters(filters)
        self.assertEqual(self.hub.status()["filters"]["repo"], "owner/repo")

    def test_failed_sync_preserves_cache_and_timestamp(self):
        before = self.hub.detail("owner/repo", 2)
        timestamp = self.hub.status()["repositories"][0]["synced_at"]
        self.github.data = []
        self.github.error = GitHubError("API rate limited")
        with self.assertRaises(HubError):
            self.hub.sync("owner/repo")
        self.assertEqual(self.hub.detail("owner/repo", 2), before)
        repo = self.hub.status()["repositories"][0]
        self.assertEqual(repo["synced_at"], timestamp)
        self.assertEqual(repo["sync_error"], "API rate limited")

    def test_snapshot_transaction_rolls_back_incomplete_save(self):
        self.github.data[0]["title"] = "Changed"
        del self.github.data[1]["title"]
        with self.assertRaises(KeyError):
            self.hub.sync("owner/repo")
        self.assertEqual(self.hub.detail("owner/repo", 1)["title"], fixture()[0]["title"])

    def test_draft_survives_sync_and_restart(self):
        self.hub.save_draft("owner/repo", 1, "下書き\nMarkdown **test**")
        self.hub.sync("owner/repo")
        self.db.close()
        self.db = Database(self.root / "data", self.root / "backups")
        self.hub = Hub(self.db, self.github)
        self.assertEqual(self.hub.detail("owner/repo", 1)["draft"], "下書き\nMarkdown **test**")

    def test_submission_same_id_is_sent_once(self):
        self.hub.save_draft("owner/repo", 1, "確認しました")
        attempt = str(uuid.uuid4())
        result = self.hub.submit("owner/repo", 1, "確認しました", attempt)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(self.hub.submit("owner/repo", 1, "確認しました", attempt)["comment_id"], 99)
        self.assertEqual(len(self.github.posts), 1)
        self.assertEqual(self.hub.detail("owner/repo", 1)["draft"], "")
        self.assertEqual(self.hub.detail("owner/repo", 1)["comments"][0]["body"], "確認しました")
        with self.assertRaises(HubError):
            self.hub.submit("owner/repo", 1, "Different", attempt)

    def test_uncertain_submission_blocks_retry_and_sync_reconciles(self):
        self.hub.save_draft("owner/repo", 1, "送信予定")
        self.github.post_error = GitHubError("Network lost", uncertain=True)
        attempt = str(uuid.uuid4())
        self.assertEqual(self.hub.submit("owner/repo", 1, "送信予定", attempt)["status"], "unknown")
        with self.assertRaises(HubError):
            self.hub.submit("owner/repo", 1, "送信予定", str(uuid.uuid4()))
        self.hub.save_draft("owner/repo", 1, "次の下書き")
        self.hub.sync("owner/repo")
        item = self.hub.detail("owner/repo", 1)
        self.assertEqual(item["submissions"][0]["status"], "sent")
        self.assertEqual(item["draft"], "次の下書き")
        self.assertEqual(len(self.github.posts), 1)

    def test_failed_submission_keeps_draft(self):
        self.hub.save_draft("owner/repo", 1, "残す本文")
        self.github.post_error = GitHubError("Permission denied")
        self.assertEqual(self.hub.submit("owner/repo", 1, "残す本文", str(uuid.uuid4()))["status"], "failed")
        self.assertEqual(self.hub.detail("owner/repo", 1)["draft"], "残す本文")

    def test_crash_marks_pending_submission_unknown(self):
        attempt = str(uuid.uuid4())
        self.db.write(lambda c: c.execute("INSERT INTO submissions(id,repo,number,body,status,created_at) VALUES(?,?,?,?,?,?)",
                                         (attempt, "owner/repo", 1, "body", "sending", "2026-10-05")))
        self.db.close()
        self.db = Database(self.root / "data", self.root / "backups")
        self.hub = Hub(self.db, self.github)
        self.assertEqual(self.hub.detail("owner/repo", 1)["submissions"][0]["status"], "unknown")
        self.hub.dismiss_unknown(attempt)
        self.assertEqual(self.hub.detail("owner/repo", 1)["submissions"][0]["status"], "acknowledged")

    def test_backup_failure_keeps_previous_and_db_write(self):
        path = self.root / "backups" / "latest.sqlite3"
        previous = path.read_bytes()
        with patch("hub.db.os.replace", side_effect=OSError("no space")):
            self.hub.save_draft("owner/repo", 1, "saved but not backed up")
        self.assertTrue(self.db.backup_error)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.hub.detail("owner/repo", 1)["draft"], "saved but not backed up")
        self.db.backup()
        self.assertIsNone(self.db.backup_error)
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT body FROM drafts").fetchone()[0], "saved but not backed up")

    def test_restore_roundtrip_rejects_running_and_requires_replace(self):
        self.hub.save_draft("owner/repo", 1, "restore me")
        backup = self.root / "backups" / "latest.sqlite3"
        with self.assertRaises(RuntimeError):
            restore(backup, self.root / "data", replace=True)
        with self.assertRaises(RuntimeError):
            Database(self.root / "data", self.root / "backups")
        restored = self.root / "restored"
        restore(backup, restored)
        with self.assertRaises(RuntimeError):
            restore(backup, restored)
        restored_db = Database(restored, self.root / "second-backup")
        try:
            self.assertEqual(Hub(restored_db, self.github).detail("owner/repo", 1)["draft"], "restore me")
        finally:
            restored_db.close()

    def test_corrupt_backup_does_not_replace_target(self):
        bad = self.root / "bad.sqlite3"
        bad.write_bytes(b"not a sqlite database")
        data = self.root / "restore-data"
        data.mkdir()
        target = data / "hub.sqlite3"
        target.write_bytes(b"original")
        with self.assertRaises(sqlite3.DatabaseError):
            restore(bad, data, replace=True)
        self.assertEqual(target.read_bytes(), b"original")

    def test_corrupt_database_not_silently_replaced(self):
        data = self.root / "corrupt-data"
        data.mkdir()
        path = data / "hub.sqlite3"
        path.write_bytes(b"broken DB")
        with self.assertRaises(sqlite3.DatabaseError):
            Database(data, self.root / "corrupt-backups")
        self.assertEqual(path.read_bytes(), b"broken DB")

    def test_token_not_in_status_or_database_or_backup(self):
        token = self.github.token.encode()
        self.assertNotIn(self.github.token, json.dumps(self.hub.status()))
        self.assertNotIn(token, self.db.path.read_bytes())
        self.assertNotIn(token, (self.root / "backups" / "latest.sqlite3").read_bytes())

    def test_invalid_targets_and_empty_body_cannot_post(self):
        for repo in ("../etc", "owner/..", "https://github.com/o/r", "owner/repo?bad=1", None):
            with self.assertRaises(HubError):
                self.hub.add_repository(repo)
        for number in (0, -1, True, "1"):
            with self.assertRaises(HubError):
                self.hub.save_draft("owner/repo", number, "body")
        with self.assertRaises(HubError):
            self.hub.submit("owner/repo", 1, " ", str(uuid.uuid4()))
        self.assertFalse(self.github.posts)


class GitHubTests(unittest.TestCase):
    def test_pagination_and_merged_detection(self):
        github = GitHub()
        with patch.object(github, "request", side_effect=[[{"id": n} for n in range(100)], [{"id": 100}]]) as request:
            self.assertEqual(len(github.pages("/repos/o/r/issues?state=all")), 101)
            self.assertIn("page=2", request.call_args_list[1].args[0])
        issue = {"number": 2, "pull_request": {}, "state": "closed", "title": "PR", "body": None,
                 "labels": [{"name": "label"}], "updated_at": "today", "user": {"login": "author"}}
        with patch.object(github, "pages", side_effect=[[issue], [{"number": 2, "merged_at": "today"}], []]):
            self.assertEqual(github.snapshot("o/r")[0]["state"], "merged")

    def test_timeout_and_http_error_sanitize_and_classify(self):
        github = GitHub("secret-token")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("secret-token detail")):
            with self.assertRaises(GitHubError) as captured:
                github.post_comment("o/r", 1, "body")
            self.assertTrue(captured.exception.uncertain)
            self.assertNotIn("secret-token", str(captured.exception))
        error = urllib.error.HTTPError("url", 403, "secret-token detail", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(GitHubError) as captured:
                github.post_comment("o/r", 1, "body")
            self.assertFalse(captured.exception.uncertain)
            self.assertNotIn("secret-token", str(captured.exception))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "data", root / "backups")
        self.github = FakeGitHub()
        self.server = make_server(Hub(self.db, self.github), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.db.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        options = {} if body is None else {"data": json.dumps(body).encode()}
        request = urllib.request.Request(self.base + path, headers=headers or {}, **options)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_http_full_workflow(self):
        headers = {"Content-Type": "application/json", "X-Hub-Request": "1", "Origin": self.base}
        self.request("/api/repositories", {"repo": "owner/repo"}, headers)
        self.assertEqual(self.request("/api/sync", {"repo": "owner/repo"}, headers)["count"], 2)
        self.request("/api/draft", {"repo": "owner/repo", "number": 1, "body": "HTTP draft"}, headers)
        self.assertEqual(self.request("/api/item?repo=owner/repo&number=1")["draft"], "HTTP draft")
        payload = {"repo": "owner/repo", "number": 1, "body": "HTTP draft", "id": str(uuid.uuid4())}
        self.assertEqual(self.request("/api/submit", payload, headers)["status"], "sent")
        self.assertEqual(self.request("/api/submit", payload, headers)["status"], "sent")
        self.assertEqual(len(self.github.posts), 1)

    def test_reject_cross_origin_wrong_host_and_missing_header(self):
        variants = [{"Content-Type": "application/json"},
                    {"Content-Type": "application/json", "X-Hub-Request": "1", "Origin": "https://evil.example"},
                    {"Content-Type": "application/json", "X-Hub-Request": "1", "Host": "evil.example:8765"}]
        for headers in variants:
            with self.assertRaises(urllib.error.HTTPError) as captured:
                self.request("/api/repositories", {"repo": "owner/repo"}, headers)
            self.assertEqual(captured.exception.code, 403)
        self.assertFalse(self.db.rows("SELECT * FROM repositories"))

    def test_static_headers_and_no_path_traversal(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn(b"GitHub Local Hub", response.read())
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as captured:
            self.request("/../../hub/db.py")
        self.assertEqual(captured.exception.code, 404)

    def test_index_html_alias_uses_relative_assets(self):
        with urllib.request.urlopen(self.base + "/index.html") as response:
            html = response.read().decode()
            self.assertIn('href="./style.css"', html)
            self.assertIn('src="./app.js"', html)


class PreviewStartupTests(unittest.TestCase):
    def test_dev_cli_starts_with_isolated_storage_and_custom_port(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            environment["GITHUB_TOKEN"] = ""
            for key in ("HUB_DATA", "HUB_BACKUPS", "HUB_HOST", "HUB_PORT"):
                environment.pop(key, None)
            process = subprocess.Popen([sys.executable, "-m", "hub.dev", "--port", "0"],
                                       cwd=directory, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                line = process.stdout.readline().strip()
                self.assertTrue(line.startswith("GitHub Local Hub: http://localhost:"), line)
                base = line.removeprefix("GitHub Local Hub: ")
                with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
                    self.assertTrue(json.load(response)["ok"])
                self.assertTrue((Path(directory) / ".preview/data/hub.sqlite3").is_file())
                self.assertTrue((Path(directory) / ".preview/backups/latest.sqlite3").is_file())
                self.assertFalse((Path(directory) / "data").exists())
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
