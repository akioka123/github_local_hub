# GitHub Local Hub

複数リポジトリのIssueとPRを、ラベルで絞って確認するローカルアプリ。本文・通常コメント、Markdown下書き、確認後のコメント投稿、SQLite保存と最新バックアップに対応します。

## 起動

DockerとDocker Composeを用意し、このフォルダーで実行します。

```sh
cp .env.example .env
# .env の GITHUB_TOKEN にGitHubのfine-grained PATを設定
docker compose up --build -d
```

[http://localhost:8765](http://localhost:8765) を開き、`owner/repository` を追加して「更新」を押します。Windowsでは `.env.example` を `.env` にコピーして同じComposeコマンドを実行してください。ポートは `.env` の `HUB_PORT` で変更できます。

トークンは対象リポジトリを選び、Metadata read、Issues read/write、Pull requests readを付与します。閲覧のみならIssuesはreadで足ります。組織の承認・SSO等が必要な場合はGitHub側で設定してください。公開リポジトリの取得だけなら空でも起動できますが、未認証のAPI上限は低くなります。設定変更後は `docker compose up -d --force-recreate` で反映します。

トークンはサーバーだけで使用します。`.env`、`data/`、`backups/` をGitに追加しないでください。アプリは個人のローカル利用を前提としています。

## 使い方

- リポジトリごとの「更新」、または「すべて更新」で同期します。定期同期はしません。
- リポジトリ・種別・状態・ラベルで絞ります。複数ラベルはいずれか一致。条件は次回起動でも保持します。
- 一覧から本文・通常コメントを確認し、GitHubのリンクを開けます。Markdownはソース表示です。
- 下書きは入力停止から約800msで保存します。「下書きを保存」でも保存できます。
- 「投稿内容を確認」で対象と本文を確認し、「この内容で投稿」で通常コメントを投稿します。下書きの保存だけでは投稿されません。
- 送信結果が不明な場合は自動で再投稿しません。更新して照合し、それでも不明ならGitHubで確認してください。確認後の明示解除をせずに再投稿できません。照合用の非表示Markdownコメントが末尾に付加されます。
- 同期エラーでは前回のデータを残します。表示日時とエラーを確認してください。API制限・アクセス権・接続の問題を解消してから更新します。

## 保存と復旧

`data/hub.sqlite3` がDB、`backups/latest.sqlite3` が最新の整合性検査済みバックアップです。全DB更新後に更新します。バックアップ失敗でもDBは保存され、画面に警告が出ます。保存先の権限・空き容量を確認して「バックアップを更新」を押してください。

`docker compose down` やコンテナ削除でも、ホストのこの2フォルダーが残ればデータは保持されます。新しいPCや別のチェックアウトでは両フォルダーを移してください。最新1件の同一ディスク保存なので、ディスク故障には別媒体へのコピーが必要です。

DBを失った場合、アプリを停止して復元します。

```sh
docker compose down
docker compose run --rm --no-deps hub python -m hub.restore --backup /backups/latest.sqlite3 --data /data
docker compose up -d
```

破損した既存DBがある場合は、まず `data/` と `backups/` を別の場所へ退避し、復元コマンドの最後に `--replace` を付けます。DBの自動置換は行いません。SQLiteの回復用ファイルが残る場合は拒否するので、退避後にそのDBの復旧を検討してください。起動中の復元と同じデータを使う二重起動は拒否します。

復元後にリポジトリ、下書き、送信履歴を確認し、GitHub同期を行ってください。送信中のバックアップを復元した場合は結果不明として扱い、GitHubで確認します。

## 開発と検証

Python 3.12以上だけで直接起動できます。

```sh
python -m hub
python -m unittest discover -s tests -v
node --check hub/static/app.js
```

ウェブプレビューでもPythonのアプリサーバーを起動して [http://localhost:8765](http://localhost:8765) を開きます。`npm run dev` / `npm start` でも起動できます（`python3` が必要）。`npm run dev -- --port 3000` のようにプレビュー用のポートも指定できます。HTMLだけを開いた場合はレイアウトを確認できますが、DB・同期・下書き操作にはAPIサーバーが必要です。CSS・JavaScript・APIは現在の表示URLを基準に読み込み、サブパスを付けるプレビュープロキシでもその配下にアクセスします。

`npm run dev` / `npm start` は `.preview/data/` と `.preview/backups/` に保存します。Docker用のDBと分けることで、Dockerが作ったファイルの所有者に影響されずに起動できます。`HUB_DATA` / `HUB_BACKUPS` が指定済みならその設定を使います。通常の保存先を使う実運用はDocker Composeまたは `python -m hub` で起動します。

任意のブラウザ操作テストは、開発環境にPlaywrightとChromiumを用意して `PYTHONPATH=. python tests/browser_smoke.py`（`CHROMIUM_PATH` で実行ファイルを指定可能）。Dockerの保存・復元テストは `docker compose build` の後に `python tests/docker_smoke.py`。Docker-in-Dockerで外側から公開ポートへ届かない環境では `HUB_TEST_EXEC_HTTP=1` を指定します。いずれも一時DBと模擬データを使い、実GitHubへ投稿しません。CIでも単体・HTTP・Docker検証を実行します。

UIはTailwind CSS 3を使用しています。スタイルを変更するときはNode.js 22以上を用意し、`ui/tailwind.css` と `tailwind.config.js` を編集して以下を実行します。

```sh
npm ci
npm run build:css
# 編集しながら再生成する場合: npm run watch:css
```

生成済みの `hub/static/style.css` もGitに保存します。Python起動・Docker起動時にはNode/npmも外部CDNも不要です。

直接起動時の環境変数: `GITHUB_TOKEN`、`HUB_DATA`（既定data）、`HUB_BACKUPS`（既定backups）、`HUB_HOST`（既定127.0.0.1）、`HUB_PORT`（既定8765）。Docker内は0.0.0.0で待ち受け、Composeがホストの127.0.0.1だけに公開します。外部への公開は想定していません。

[要件](docs/requirements.md)、[設計](docs/design.md)、[検証結果](docs/gates/mvp-report.md)、[共通開発規約](AGENTS.md) を参照してください。Claude Codeは `CLAUDE.md` から同じ規約を読み込みます。
