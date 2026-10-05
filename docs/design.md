# 基本設計

## 構成

ブラウザ → 同一オリジンHTTPサーバー (`hub/server.py`) → サービス (`hub/service.py`) → SQLite (`hub/db.py`) / GitHub REST (`hub/github.py`)。

UIは静的ファイル。GitHub本文を `textContent` で挿入する。GitHubリンクは検査済みリポジトリと番号から生成する。ブラウザはトークンを受け取らず、GitHubへ直接アクセスしない。

スタイルはTailwind CSS 3を開発時にコンパイルし、生成済みCSSを同梱する。`ui/tailwind.css` のコンポーネントをTailwindのspacing/color/typographyトークンで統一する。デスクトップは一覧と詳細を別々にスクロールし、スマートフォンは縦に配置する。Node/npmはUI編集時のみ必要。

## データ

| テーブル | 保存対象 |
|---|---|
| repositories | リポジトリ名、成功同期日時、最後の同期失敗 |
| items | repo+number、種別、GitHub状態、本文、タイトル、ラベル、著者、更新日時 |
| comments | 通常コメントID、本文、著者、作成日時 |
| drafts | repo+numberごとの下書き、保存日時 |
| submissions | 送信UUID、対象、本文、sending/sent/failed/unknown/acknowledged、コメントID、エラー |
| settings | 最後の絞り込み条件 |

同期はネットワーク取得完了後にトランザクションでキャッシュを置換する。下書きと送信履歴はキャッシュとは独立して保持。途中失敗で部分的な一覧を保存しない。PRはissuesとpullsの一覧を突合し、merged_atでmergedを判定する。

## 送信と障害

送信前にsendingを永続化する。GitHubへのPOSTは1回のみ。明確なHTTP拒否はfailed、通信断や5xx、不正な投稿レスポンスはunknown。起動時のsendingもunknownへ移す。同じUUIDは保存済み結果を返す。unknownがある対象への新規送信は禁止する。

送信本文の末尾に `<!-- github-local-hub:UUID -->` を付け、同期時に照合する。照合成功はsentへ更新し、一致する旧下書きだけを削除する。利用者がGitHubで確認して再投稿を許可した場合はacknowledgedへ移す。POST成功後のローカル保存失敗はsendingを残し、起動または同期で確認する。ブラウザからのネットワーク切断もサーバー側の履歴再取得を優先する。

## バックアップと復元

全DB更新後にSQLite backup APIを呼ぶ。`latest.sqlite3.tmp` の整合性検査とfsync後、同じディレクトリの `latest.sqlite3` をatomic replaceする。失敗はメモリ上の状態としてUIに返す。元DBのコミットを取り消さず、直前の成功バックアップを保持する。再起動時にもバックアップを更新する。

`python -m hub.restore` は停止中に同じデータ用ロックを取得し、読み取り専用でバックアップのintegrity_check、バージョン、必要テーブルを確認する。検査成功後に一時ファイルから置換。既存DBは明示 `--replace` なしで上書きしない。SQLite回復用ファイルが残る場合は復元を拒否する。

## API

GET: `/api/health`, `/api/status`, `/api/items`, `/api/item`。

POST: `/api/repositories`, `/api/sync`, `/api/filters`, `/api/draft`, `/api/submit`, `/api/acknowledge`, `/api/backup`。更新はJSON、`X-Hub-Request: 1`、同一Originから受け付ける。JSON本文は256KB以下。

DBアクセスはRLockで直列化。同期と投稿は別の操作用RLockで直列化し、待機中でも一覧・詳細は読み取り可能。複数プロセスはデータ用ファイルロックで拒否。APIはローカル個人用であり、外部公開用の認証・レート制限・TLS終端は備えない。
