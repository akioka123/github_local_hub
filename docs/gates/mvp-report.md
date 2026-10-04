# MVP実装・検証結果

2026-10-05 (Asia/Tokyo)。実装・検証はCodexの同一セッションで実施。

## 結果

企画のMVP範囲を実装し、ローカルの自動検証は合格。Dockerで起動可能。追加パッケージなしでPythonからも起動可能。実GitHubの認証付き同期・実投稿は未検証で、利用環境での確認が必要。

## 実装

- 複数リポジトリ登録と手動同期。Issue/PRのopen/closed/merged、本文、任意ラベル、更新日時、通常コメント。
- リポジトリ・種別・状態・複数ラベルORの絞り込みと前回条件保存。
- Markdownソースの安全な表示、GitHubリンク、下書き自動保存と明示保存。
- 対象と本文を確認して通常コメント投稿。永続送信IDによる再送抑止、結果不明時の投稿ブロックと同期照合。
- SQLite永続保存、更新後の検査済み最新バックアップ、失敗警告と再試行、停止中の復元CLI。
- Docker Compose、README、AGENTS.md / CLAUDE.md、要件・設計・工程文書、GitHub Actions。

## 実行した検証

| 検証 | 結果 | 確認内容 |
|---|---|---|
| `python -m unittest discover -s tests -v` | 19件合格 | フィルター、独自ラベル、同期失敗時の保持、transaction rollback、下書き再起動保持、二重投稿抑止、結果不明の照合と起動後回復、バックアップ失敗時の前回分保持、停止中復元、破損DB保持、トークン非保存、入力検査、GitHubページングとmerged、HTTP一連操作、Origin/Host検査、静的配信とpath traversal拒否 |
| `node --check hub/static/app.js` | 合格 | JavaScript構文 |
| `PYTHONPATH=. python tests/browser_smoke.py` | 合格 | Chromiumで登録→同期→安全な本文表示→下書き保存・対象切替→対象と本文の確認→投稿→同期失敗保持→結果不明のブロック・照合→保存フィルター再読み込み。390px幅で横方向のはみ出しなし、JS例外なし |
| `docker compose build` | 合格 | Python 3.12-slimイメージ作成 |
| `docker compose up -d` / `compose ps` | 合格 | 起動、healthy、ホスト127.0.0.1限定のポート設定 |
| `HUB_TEST_EXEC_HTTP=1 python tests/docker_smoke.py` | 合格 | 一時ホストフォルダーで保存、コンテナ削除・再作成後の保持、DB喪失後のbackup復元、復元後の起動とフィルター保持 |

テストは一時DBと模擬GitHubを使用し、実GitHubにコメントを送信していない。Dockerテストではホスト利用者が作成したフォルダーへの書き込み権限不足を検出し、Composeで全capabilityを落とした上でDAC_OVERRIDEだけを追加して再検証した。DB破損・バックアップ失敗は模擬で確認し、利用者のファイルを破損させていない。

## 環境の制約と利用開始時の確認

- 既定サンドボックスではソケット作成・Docker接続が制限されるため、承認レビューされた実行でローカルHTTP・ブラウザ・Dockerを検証した。
- Docker-in-Dockerでは内側の公開ポートに外側から接続できなかった。Docker検証は内部から同じHTTP APIを呼び、ホストでの保存内容と復元を検証した。
- 実GitHub APIの公開リポジトリ読み取りも試したが、管理環境のプロキシが `api.github.com` へのCONNECTを403 Forbiddenで拒否した。ポリシーの迂回はしていない。Gitの取得は実行可能だった。
- 利用PCから `api.github.com` に接続できること、PATの対象・Issues read/write・Pull requests read権限、必要なら組織承認・SSOを確認する。実コメント送信は利用者が画面で確認して実施する。
- Python直接起動のWindows固有ファイルロックはこのLinux環境では未実行。WindowsのDocker DesktopではLinuxコンテナとして動かす構成。
- 大規模リポジトリの同期時間・API消費、長期運用時間は未測定。定期同期、正式PRレビュー、ラベル変更、CI表示、関連Issue抽出は初版の範囲外。

起動・トークン設定・復元は [README](../../README.md) を参照。

## 保存と共有

実装はローカルの `codex/local-hub-mvp` に保存済み。Gitのpushは403 Permission denied、GitHub連携のcreate-tree APIも403 Resource not accessible by integrationで拒否されたため、リモートブランチとPRは未作成。GitHub接続に書き込み権限が必要。変更パッチと追跡ファイルのみのソースZIPを別途用意し、DB・バックアップ・認証情報を含めずに取り込める状態にした。
