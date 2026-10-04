import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import urllib.parse

from .service import HubError


STATIC = Path(__file__).parent / "static"


def make_server(hub, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Do not log URL query strings, bodies, or credentials.
            pass

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            payload = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def validate_host(self):
            raw_host = self.headers.get("Host", "")
            parsed = urllib.parse.urlsplit("http://" + raw_host)
            try:
                valid = parsed.hostname in ("localhost", "127.0.0.1", "::1") and parsed.port is not None
            except ValueError:
                valid = False
            if not valid or parsed.username or parsed.password or parsed.path:
                raise HubError("ローカルホストからアクセスしてください。", 403)

        def do_GET(self):
            self.handle_request(False)

        def do_POST(self):
            self.handle_request(True)

        def handle_request(self, write):
            try:
                self.validate_host()
                parsed = urllib.parse.urlsplit(self.path)
                if write:
                    if self.headers.get("X-Hub-Request") != "1":
                        raise HubError("リクエストの確認ヘッダーが必要です。", 403)
                    origin = self.headers.get("Origin")
                    if origin and origin != "http://" + self.headers["Host"]:
                        raise HubError("別サイトからの操作は許可されていません。", 403)
                    if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                        raise HubError("JSONリクエストが必要です。", 415)
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        raise HubError("リクエスト長が不正です。") from None
                    if length < 1 or length > 256000:
                        raise HubError("リクエストサイズが不正です。", 413)
                    try:
                        body = json.loads(self.rfile.read(length))
                    except (ValueError, UnicodeError):
                        raise HubError("JSONを読み取れません。") from None
                    if not isinstance(body, dict):
                        raise HubError("JSONオブジェクトが必要です。")
                    actions = {
                        "/api/repositories": lambda: hub.add_repository(body.get("repo")),
                        "/api/sync": lambda: hub.sync(body.get("repo")),
                        "/api/draft": lambda: hub.save_draft(body.get("repo"), body.get("number"), body.get("body")),
                        "/api/submit": lambda: hub.submit(body.get("repo"), body.get("number"), body.get("body"), body.get("id")),
                        "/api/acknowledge": lambda: hub.dismiss_unknown(body.get("id")),
                        "/api/filters": lambda: hub.save_filters(body),
                        "/api/backup": lambda: self.retry_backup(),
                    }
                    if parsed.path not in actions:
                        raise HubError("対象がありません。", 404)
                    self.send(200, actions[parsed.path]())
                    return
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    self.send(200, {"ok": True})
                elif parsed.path == "/api/status":
                    self.send(200, hub.status())
                elif parsed.path == "/api/items":
                    filters = {key: query.get(key, [""])[0] for key in ("repo", "kind", "state")}
                    filters["labels"] = query.get("label", [])
                    self.send(200, {"items": hub.list_items(filters), "labels": hub.labels()})
                elif parsed.path == "/api/item":
                    try:
                        number = int(query.get("number", [""])[0])
                    except ValueError:
                        raise HubError("番号が不正です。") from None
                    self.send(200, hub.detail(query.get("repo", [""])[0], number))
                else:
                    assets = {"/": ("index.html", "text/html; charset=utf-8"),
                              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                              "/style.css": ("style.css", "text/css; charset=utf-8")}
                    if parsed.path not in assets:
                        raise HubError("対象がありません。", 404)
                    name, mime = assets[parsed.path]
                    self.send(200, (STATIC / name).read_bytes(), mime)
            except HubError as error:
                self.send(error.status, {"error": str(error)})
            except (sqlite3.Error, OSError):
                self.send(500, {"error": "ローカル保存・読み取りに失敗しました。DBと保存先を確認してください。"})
            except Exception:
                self.send(500, {"error": "処理に失敗しました。保存済みデータを保持したまま再確認してください。"})

        def retry_backup(self):
            hub.db.backup()
            return hub.status()

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
