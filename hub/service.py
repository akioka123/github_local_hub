import json
import re
import threading
import uuid

from .db import now
from .github import GitHubError


PRESETS = ["status:triage", "status:ready", "status:in-progress", "status:needs-info",
           "status:needs-human", "status:blocked", "pr:needs-fix", "type:bug",
           "type:balance", "type:data", "type:docs", "type:infra", "p1", "p2", "p3"]


class HubError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def repository(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise HubError("リポジトリは owner/repo 形式で入力してください。")
    if value.split("/")[1] in (".", ".."):
        raise HubError("リポジトリ名が不正です。")
    return value.lower()


def item_number(value):
    if type(value) is not int or value < 1 or value > 2147483647:
        raise HubError("Issue/PR番号が不正です。")
    return value


def comment_body(value, allow_empty=False):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 60000:
        raise HubError("本文はUTF-8で60,000バイト以内にしてください。")
    if not allow_empty and not value.strip():
        raise HubError("コメント本文を入力してください。")
    return value


class Hub:
    def __init__(self, db, github):
        self.db = db
        self.github = github
        self.operation_lock = threading.RLock()

    def status(self):
        return {"repositories": self.db.rows("SELECT * FROM repositories ORDER BY name"),
                "backup_at": self.db.backup_at, "backup_error": self.db.backup_error,
                "can_post": bool(self.github.token), "presets": PRESETS,
                "filters": self.db.setting("filters", {"repo": "", "kind": "", "state": "", "labels": []})}

    def add_repository(self, value):
        repo = repository(value)
        self.db.write(lambda c: c.execute("INSERT OR IGNORE INTO repositories(name) VALUES(?)", (repo,)))
        return {"repo": repo}

    def require_item(self, repo, number):
        repo, number = repository(repo), item_number(number)
        rows = self.db.rows("SELECT * FROM items WHERE repo=? AND number=?", (repo, number))
        if not rows:
            raise HubError("対象データがありません。先にリポジトリを同期してください。", 404)
        return rows[0]

    def sync(self, value):
        repo = repository(value)
        with self.operation_lock:
            if not self.db.rows("SELECT name FROM repositories WHERE name=?", (repo,)):
                raise HubError("リポジトリが未登録です。", 404)
            try:
                try:
                    snapshot = self.github.snapshot(repo)
                except (KeyError, TypeError, ValueError):
                    raise GitHubError("GitHubの取得データを読み取れませんでした。前回のデータを保持しています。") from None

                def save(c):
                    # Replace cache only after ALL network reads succeeded.
                    c.execute("DELETE FROM items WHERE repo=?", (repo,))
                    for item in snapshot:
                        c.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)", (
                            repo, item["number"], item["kind"], item["state"], item["title"],
                            item["body"], json.dumps(item["labels"]), item["updated_at"], item["author"]))
                        for comment in item["comments"]:
                            self.cache_comment(c, repo, item["number"], comment)
                            for attempt in c.execute(
                                    "SELECT id FROM submissions WHERE repo=? AND number=? AND status IN ('sending','unknown')",
                                    (repo, item["number"])).fetchall():
                                if self.marker(attempt[0]) in (comment.get("body") or ""):
                                    c.execute("UPDATE submissions SET status='sent', comment_id=?, error=NULL WHERE id=?",
                                              (comment["id"], attempt[0]))
                                    # Keep any newer or changed draft intact.
                                    c.execute("DELETE FROM drafts WHERE repo=? AND number=? AND body=(SELECT body FROM submissions WHERE id=?)",
                                              (repo, item["number"], attempt[0]))
                    c.execute("UPDATE repositories SET synced_at=?, sync_error=NULL WHERE name=?", (now(), repo))
                self.db.write(save)
            except GitHubError as error:
                self.db.write(lambda c: c.execute("UPDATE repositories SET sync_error=? WHERE name=?", (str(error), repo)))
                raise HubError(str(error), 502) from None
        return {"count": len(snapshot), "repo": repo}

    def list_items(self, filters):
        conditions, args = [], []
        for column in ("repo", "kind", "state"):
            value = filters.get(column, "")
            if value:
                conditions.append(f"i.{column}=?")
                args.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.db.rows("""SELECT i.*, r.synced_at, r.sync_error,
          EXISTS(SELECT 1 FROM drafts d WHERE d.repo=i.repo AND d.number=i.number AND d.body<>'') AS has_draft
          FROM items i JOIN repositories r ON r.name=i.repo""" + where + " ORDER BY i.updated_at DESC, i.repo, i.number", args)
        selected = filters.get("labels", [])
        for row in rows:
            row["labels"] = json.loads(row["labels"])
        return [row for row in rows if not selected or set(selected).intersection(row["labels"])]

    def labels(self):
        labels = set(PRESETS)
        for row in self.db.rows("SELECT labels FROM items"):
            labels.update(json.loads(row["labels"]))
        return sorted(labels)

    def detail(self, repo, number):
        item = self.require_item(repo, number)
        item["labels"] = json.loads(item["labels"])
        item["comments"] = self.db.rows("SELECT * FROM comments WHERE repo=? AND number=? ORDER BY created_at,id", (item["repo"], number))
        for comment in item["comments"]:
            comment["body"] = re.sub(r"\n\n<!-- github-local-hub:[0-9a-f-]+ -->$", "", comment["body"])
        drafts = self.db.rows("SELECT body FROM drafts WHERE repo=? AND number=?", (item["repo"], number))
        item["draft"] = drafts[0]["body"] if drafts else ""
        item["submissions"] = self.db.rows("SELECT * FROM submissions WHERE repo=? AND number=? ORDER BY created_at DESC, rowid DESC", (item["repo"], number))
        return item

    def save_draft(self, repo, number, body):
        item = self.require_item(repo, number)
        body = comment_body(body, allow_empty=True)
        self.db.write(lambda c: c.execute("INSERT INTO drafts VALUES(?,?,?,?) ON CONFLICT(repo,number) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                                        (item["repo"], number, body, now())))
        return {"saved_at": now()}

    @staticmethod
    def marker(attempt_id):
        return f"<!-- github-local-hub:{attempt_id} -->"

    @staticmethod
    def cache_comment(c, repo, number, comment):
        c.execute("INSERT OR REPLACE INTO comments VALUES(?,?,?,?,?,?)", (
            repo, number, comment["id"], comment.get("body") or "",
            (comment.get("user") or {}).get("login", ""), comment.get("created_at") or now()))

    def submit(self, repo, number, body, attempt_id):
        item = self.require_item(repo, number)
        repo = item["repo"]
        body = comment_body(body)
        try:
            if str(uuid.UUID(attempt_id)) != attempt_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HubError("送信IDが不正です。") from None
        with self.operation_lock:
            previous = self.db.rows("SELECT * FROM submissions WHERE id=?", (attempt_id,))
            if previous:
                old = previous[0]
                if (old["repo"], old["number"], old["body"]) != (repo, number, body):
                    raise HubError("送信IDは別の内容で使用済みです。", 409)
                return old
            unresolved = self.db.rows("SELECT id FROM submissions WHERE repo=? AND number=? AND status IN ('sending','unknown')", (repo, number))
            if unresolved:
                raise HubError("結果不明の送信があります。同期またはGitHubで確認してください。", 409)
            if not self.github.token:
                raise HubError("コメント投稿にはGITHUB_TOKENの設定が必要です。")
            self.db.write(lambda c: c.execute("INSERT INTO submissions(id,repo,number,body,status,created_at) VALUES(?,?,?,?,?,?)",
                                            (attempt_id, repo, number, body, "sending", now())))
            try:
                result = self.github.post_comment(repo, number, body + "\n\n" + self.marker(attempt_id))
            except GitHubError as error:
                status = "unknown" if error.uncertain else "failed"
                self.db.write(lambda c: c.execute("UPDATE submissions SET status=?, error=? WHERE id=?", (status, str(error), attempt_id)))
                return self.db.rows("SELECT * FROM submissions WHERE id=?", (attempt_id,))[0]

            def save(c):
                c.execute("UPDATE submissions SET status='sent', comment_id=? WHERE id=?", (result["id"], attempt_id))
                self.cache_comment(c, repo, number, result)
                c.execute("DELETE FROM drafts WHERE repo=? AND number=? AND body=?", (repo, number, body))
            self.db.write(save)
            return self.db.rows("SELECT * FROM submissions WHERE id=?", (attempt_id,))[0]

    def dismiss_unknown(self, attempt_id):
        with self.operation_lock:
            self.db.write(lambda c: c.execute("UPDATE submissions SET status='acknowledged', error=? WHERE id=? AND status='unknown'",
                                            ("利用者がGitHubで結果を確認し、再投稿を許可しました。", attempt_id)))
        return {"ok": True}

    def save_filters(self, filters):
        if not isinstance(filters, dict):
            raise HubError("フィルターが不正です。")
        repo = repository(filters["repo"]) if filters.get("repo") else ""
        kind, state, labels = filters.get("kind", ""), filters.get("state", ""), filters.get("labels", [])
        if kind not in ("", "issue", "pr") or state not in ("", "open", "closed", "merged"):
            raise HubError("フィルターが不正です。")
        if not isinstance(labels, list) or len(labels) > 100 or any(not isinstance(s, str) or len(s) > 100 for s in labels):
            raise HubError("ラベル指定が不正です。")
        value = {"repo": repo, "kind": kind, "state": state, "labels": labels}
        self.db.write(lambda c: c.execute("INSERT INTO settings VALUES('filters',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(value),)))
        return value
