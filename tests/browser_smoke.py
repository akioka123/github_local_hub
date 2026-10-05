"""Optional browser E2E: PYTHONPATH=. python tests/browser_smoke.py.

Requires development-only Playwright and a local Chromium; not needed by the app.
Uses a temporary DB and fake GitHub. Never posts to a real repository.
"""
import os
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright, expect

from hub.db import Database
from hub.github import GitHubError
from hub.server import make_server
from hub.service import Hub
from test_hub import FakeGitHub


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = Database(root / "data", root / "backups")
        github = FakeGitHub()
        server = make_server(Hub(db, github), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                page.get_by_placeholder("owner/repository").fill("owner/repo")
                page.get_by_role("button", name="追加", exact=True).click()
                page.get_by_role("button", name="更新", exact=True).click()
                page.locator(".item").filter(has_text="同期の状況を確認する").click()
                page.wait_for_selector("#draft")
                assert "<script>alert(1)</script>" in page.locator("#detail .text-body").first.inner_text()
                assert not page.locator("#detail script").count()
                page.locator("#draft").fill("ブラウザからの下書き **Markdown**")
                page.get_by_role("button", name="下書きを保存", exact=True).click()
                expect(page.locator("#draft-state")).to_contain_text("保存済み")
                page.locator(".item").filter(has_text="バックアップを改善する").click()
                expect(page.locator("#detail h2")).to_contain_text("バックアップ")
                page.locator(".item").filter(has_text="同期の状況を確認する").click()
                expect(page.locator("#draft")).to_have_value("ブラウザからの下書き **Markdown**")
                assert not github.posts
                page.get_by_role("button", name="投稿内容を確認", exact=True).click()
                page.wait_for_selector("dialog[open]")
                assert "owner/repo #1" in page.locator("#confirm-target").inner_text()
                assert page.locator("#confirm-body").inner_text() == "ブラウザからの下書き **Markdown**"
                assert not github.posts
                page.get_by_role("button", name="この内容で投稿", exact=True).click()
                expect(page.locator(".attempt").first).to_contain_text("投稿済み")
                assert len(github.posts) == 1
                assert page.locator("#draft").input_value() == ""
                screenshot = os.environ.get("HUB_SCREENSHOT")
                if screenshot:
                    page.screenshot(path=screenshot, full_page=True)
                # Failed synchronization keeps cached data and displays stale state.
                github.error = GitHubError("模擬API制限")
                page.get_by_role("button", name="更新", exact=True).click()
                expect(page.locator("#repositories")).to_contain_text("前回のデータ")
                assert page.locator(".item").count() == 2
                github.error = None
                # Ambiguous send is blocked, then reconciled from the remote marker.
                github.post_error = GitHubError("模擬通信断", uncertain=True)
                page.locator("#draft").fill("結果不明の投稿")
                page.get_by_role("button", name="投稿内容を確認", exact=True).click()
                page.get_by_role("button", name="この内容で投稿", exact=True).click()
                expect(page.locator("#detail")).to_contain_text("送信結果不明")
                assert page.locator("#open-send").is_disabled()
                assert len(github.posts) == 2
                page.get_by_role("button", name="更新", exact=True).click()
                expect(page.locator("#open-send")).to_be_enabled()
                assert len(github.posts) == 2
                page.locator("#filter-state").select_option("merged")
                expect(page.locator(".item")).to_have_count(1)
                page.reload()
                expect(page.locator("#filter-state")).to_have_value("merged")
                assert page.locator(".item").count() == 1
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                browser.close()
            print("Browser E2E passed: register, sync, safe body, draft persistence, confirmed post, stale cache, uncertain-send reconciliation, saved filter, mobile layout.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            db.close()


if __name__ == "__main__":
    main()
