import json
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class GitHub:
    def __init__(self, token=""):
        self.token = token

    def request(self, path, method="GET", body=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-local-hub/1.0",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request("https://api.github.com" + path, data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
                return result
        except urllib.error.HTTPError as error:
            messages = {
                401: "GitHub認証に失敗しました。トークンを確認してください。",
                403: "GitHubへのアクセスが制限されています。権限・API制限を確認してください。",
                404: "GitHubの対象が見つかりません。名前とアクセス権を確認してください。",
                422: "GitHubが入力を受け付けませんでした。内容と対象を確認してください。",
                429: "GitHubのAPI制限に達しました。時間を置いて更新してください。",
            }
            raise GitHubError(messages.get(error.code, f"GitHub APIエラー (HTTP {error.code})"),
                              uncertain=method == "POST" and error.code >= 500) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise GitHubError("GitHubとの通信に失敗しました。接続を確認してください。",
                              uncertain=method == "POST") from None

    def pages(self, path):
        result = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 1001):
            batch = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise GitHubError("GitHubから不正な一覧レスポンスを受信しました。")
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise GitHubError("取得件数が上限を超えました。今回の同期は保存していません。")

    def snapshot(self, repo):
        issues = self.pages(f"/repos/{repo}/issues?state=all&sort=updated&direction=desc")
        pulls = {p["number"]: p for p in self.pages(f"/repos/{repo}/pulls?state=all")}
        result = []
        for issue in issues:
            number = issue["number"]
            is_pr = "pull_request" in issue
            pull = pulls.get(number)
            if is_pr and pull is None:
                # Resolve list races instead of displaying a merged PR as closed.
                pull = self.request(f"/repos/{repo}/pulls/{number}")
            state = "merged" if is_pr and pull.get("merged_at") else issue["state"]
            result.append({
                "number": number, "kind": "pr" if is_pr else "issue", "state": state,
                "title": issue["title"], "body": issue.get("body") or "",
                "labels": [label["name"] for label in issue.get("labels", [])],
                "updated_at": issue["updated_at"], "author": (issue.get("user") or {}).get("login", ""),
                "comments": self.pages(f"/repos/{repo}/issues/{number}/comments"),
            })
        return result

    def post_comment(self, repo, number, body):
        if not self.token:
            raise GitHubError("コメント投稿にはGITHUB_TOKENの設定が必要です。")
        result = self.request(f"/repos/{repo}/issues/{number}/comments", "POST", {"body": body})
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise GitHubError("投稿結果を確認できませんでした。同期して確認してください。", uncertain=True)
        return result
