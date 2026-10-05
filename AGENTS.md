# github_local_hub

個人用のローカルGitHub Issue/PR管理アプリ。Python標準ライブラリ、SQLite、素のJavaScriptを使用する。

UIはTailwind CSS 3。`ui/tailwind.css` と `tailwind.config.js` を編集し、`npm ci` → `npm run build:css` で `hub/static/style.css` を生成する。生成CSSもコミットする。Node/npmはUI開発だけで使用し、アプリ実行時の依存にしない。

- 要件: `docs/requirements.md`、設計: `docs/design.md`、工程: `docs/gates/gate-status.md`。
- 起動: `docker compose up --build -d`。開発時は `python -m hub`。
- 検証: `python -m unittest discover -s tests -v`、`node --check hub/static/app.js`。
- GitHubトークンは実行時の `GITHUB_TOKEN` のみ。ブラウザ、DB、バックアップ、ログ、Gitに保存しない。
- 外部への書き込みは利用者が対象と本文を確認し、送信を押したときだけ行う。ラベル変更、レビュー、マージ、エージェント起動は範囲外。
- SQLite更新後にbackup APIでスナップショットを作り、同じディレクトリでatomic replaceする。失敗時は以前のバックアップを保持する。
- DBの破損は起動エラーにする。空DBへの自動置換をしない。復元は停止中に明示的に行う。
- `data/`、`backups/`、`.env`、個人設定、ワークツリーは追跡しない。
- 実データへの投稿をテストしない。GitHubモックと一時DBで検証する。
- CodexとClaude Codeの両方で本規約を参照する。共通スキルをコピーしない。
