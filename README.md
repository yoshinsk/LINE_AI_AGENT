# LINE AI Agent

LINE公式アカウントのWebhookをVPSで受け、MariaDBに会話・添付・ジョブを保存し、Windows常駐ワーカーがCodexを実行してLINEへ返信するAIエージェントです。

## 構成

```text
LINE
  -> https://example.com/line/
     -> PHP Webhook入口
     -> MariaDB db_codex_line
     -> Windowsワーカーが internal.php からジョブ取得
     -> Codex CLI実行
     -> internal.php complete
     -> LINE push message
```

VPS側はPlesk公開ディレクトリに置けるPHPだけで動かします。Windows側は既存のCodex実行環境を使うため、VPSにCodex CLIを置く必要はありません。

## 主な機能

- LINE Webhookの署名検証
- 1対1トークの通常会話
- グループ・ルームでは `AI:` / `@AI` / `プロジェクト:` など明示呼び出しのみ処理
- `プロジェクト: alias` / `project: alias` による作業ディレクトリ指定
- `プロジェクト一覧` のローカル応答
- `現状報告` による同一トーク内の未完了ジョブ確認
- 会話履歴とAI回答をMariaDBへ保存
- `FULLTEXT` とLIKEフォールバックによる過去ナレッジ検索
- 画像、動画、音声、ファイル添付の保存とCodexへのローカルパス連携

## 添付ファイル

LINEの `file` / `image` / `video` / `audio` メッセージを受けると、VPS側がLINEのcontent取得APIでバイナリを取得し、公開root外の `private/attachments` に保存します。Windowsワーカーは内部APIの認証付きdownload endpointから添付を取得し、Codexプロンプトへ絶対パスとして渡します。

既定では以下を拒否します。

- 実行ファイル、スクリプト、DLL/JAR
- マクロ付きOffice
- zip/rar/7z/tar/gz等のアーカイブ
- 設定上限を超えるファイル

許可拡張子を固定したい場合はVPS側envの `LINE_AI_AGENT_ATTACHMENT_ALLOWED_EXTENSIONS` を設定します。

## VPS配置

公開先:

```text
/path/to/vhost/public/line/
```

非公開設定:

```text
/path/to/vhost/private/line-ai-agent.env
```

添付保存:

```text
/path/to/vhost/private/attachments/
```

DB初期化:

```bash
mysql -u db_codex_line -p db_codex_line < deploy/schema.sql
```

Webhook URL:

```text
https://example.com/line/
```

## VPS側env

`deploy/line-ai-agent.env.example` を参考に、公開root外へ `line-ai-agent.env` を作成します。実値はGitに入れません。

必要項目:

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_AI_AGENT_DB_HOST`
- `LINE_AI_AGENT_DB_NAME`
- `LINE_AI_AGENT_DB_USER`
- `LINE_AI_AGENT_DB_PASS`
- `LINE_AI_AGENT_WORKER_TOKEN`

## Windowsワーカー設定

```powershell
Copy-Item .env.example .env
notepad .env
```

最低限、以下を設定します。

- `LINE_AGENT_API_BASE_URL=https://example.com/line/internal.php`
- `LINE_AI_AGENT_WORKER_TOKEN`
- `CODEX_COMMAND`
- `CODEX_PROJECTS_JSON`
- `CODEX_ALLOWED_PROJECT_ROOTS`

実行:

```powershell
$env:PYTHONPATH = "src"
python -m line_ai_agent --env .env health
python -m line_ai_agent --env .env once
python -m line_ai_agent --env .env serve
```

バックグラウンド起動:

```powershell
.\scripts\start-worker.ps1
.\scripts\status-worker.ps1
.\scripts\stop-worker.ps1
```

## 検証

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m compileall src tests
```

VPS側は配置後に以下を確認します。

```bash
/opt/plesk/php/8.5/bin/php -l public/line/index.php
/opt/plesk/php/8.5/bin/php -l public/line/internal.php
curl -sS https://example.com/line/
```

## 公式仕様の前提

- LINE PlatformはWebhook URLへHTTPS POSTします。
- Webhookは `x-line-signature` をraw bodyから検証します。
- 重い処理は非同期化します。
- 添付バイナリはWebhookで受け取るmessage IDから `GET /v2/bot/message/{messageId}/content` で取得します。
- 返信はreply message、処理完了後はpush messageを使います。

参照:

- https://developers.line.biz/en/docs/messaging-api/receiving-messages/
- https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/
- https://developers.line.biz/en/reference/messaging-api/

