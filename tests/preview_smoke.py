"""Optional browser check for a preview mounted under a reverse-proxy prefix."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright, expect

from hub.db import Database
from hub.server import make_server
from hub.service import Hub
from test_hub import FakeGitHub


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = Database(root / "data", root / "backups")
        github = FakeGitHub()
        backend = make_server(Hub(db, github), port=0)
        backend_base = f"http://127.0.0.1:{backend.server_port}"

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def forward(self):
                if self.path.startswith("/static-preview/"):
                    name = self.path[len("/static-preview/"):]
                    mime = {"index.html": "text/html", "style.css": "text/css", "app.js": "text/javascript"}
                    if self.command != "GET" or name not in mime:
                        self.send_error(404)
                        return
                    content = (Path(__file__).resolve().parents[1] / "hub/static" / name).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime[name])
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                if not self.path.startswith("/preview/"):
                    self.send_error(404)
                    return
                path = self.path[len("/preview"):]
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))) if self.command == "POST" else None
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() in ("host", "origin", "content-type", "x-hub-request")}
                request = urllib.request.Request(backend_base + path, data=body, headers=headers)
                try:
                    response = urllib.request.urlopen(request, timeout=5)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    content = response.read()
                    self.send_response(response.code)
                    for key in ("Content-Type", "Content-Security-Policy", "X-Frame-Options"):
                        if response.headers.get(key):
                            self.send_header(key, response.headers[key])
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        servers = [backend, proxy]
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
        for thread in threads:
            thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                base = f"http://127.0.0.1:{proxy.server_port}/preview/"
                page.goto(base + "index.html")
                expect(page.locator(".topbar")).to_have_css("background-color", "rgb(2, 6, 23)")
                expect(page.locator("#auth-status")).to_contain_text("設定済み")
                page.get_by_placeholder("owner/repository").fill("owner/repo")
                page.get_by_role("button", name="追加", exact=True).click()
                page.get_by_role("button", name="更新", exact=True).click()
                expect(page.locator(".item")).to_have_count(2)
                page.locator(".item").filter(has_text="同期の状況を確認する").click()
                page.locator("#draft").fill("サブパス経由の下書き")
                page.get_by_role("button", name="下書きを保存", exact=True).click()
                expect(page.locator("#draft-state")).to_contain_text("保存済み")
                assert all(url.startswith(base) for url in requests if "/api/" in url or url.endswith(("style.css", "app.js")))
                assert not errors, errors
                assert not github.posts
                # A static-only preview still loads Tailwind and gives an
                # actionable message instead of a JSON parse error.
                page.goto(f"http://127.0.0.1:{proxy.server_port}/static-preview/index.html")
                expect(page.locator(".topbar")).to_have_css("background-color", "rgb(2, 6, 23)")
                expect(page.locator("#notice")).to_contain_text("HTMLだけのプレビュー")
                expect(page.locator("#sync-all")).to_be_disabled()
                browser.close()
            print("Preview check passed: prefixed HTML/CSS/JS/API, register/sync/draft, static-only styles and API guidance.")
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()
            db.close()


if __name__ == "__main__":
    main()
