# LINE AI Agent

LINE公式アカウントのWebhookをHTTPS公開サーバで受け、MariaDB/MySQL互換DBに会話・添付・ジョブを保存し、常駐ワーカーがCodexを実行してLINEへ返信するAIエージェントです。

## 構成

```text
LINE
  -> https://example.com/line/
     -> PHP Webhook入口
     -> MariaDB/MySQL互換DB
     -> ワーカーが internal.php からジョブ取得
     -> Codex CLI実行
     -> internal.php complete
     -> LINE push message
```

サーバ側はPHPが動作するWeb公開ディレクトリに配置できます。特定のコントロールパネルやホスティング製品には依存しません。Codex CLIはワーカー側で実行するため、公開サーバにCodex CLIを置く必要はありません。

## 主な機能

- LINE Webhookの署名検証
- 1対1トークの通常会話
- グループ・ルームではbot自身へのLINEメンション、bot発言へのLINE返信、`AI:` / `@AI` / `プロジェクト:` など明示呼び出しのみ処理
- `プロジェクト: alias` / `project: alias` による作業ディレクトリ指定
- `プロジェクト一覧` のローカル応答
- `現状報告` による同一トーク内の未完了ジョブ確認
- 長文、添付、プロジェクト指定など時間がかかる依頼だけ受付返信を送信
- 会話履歴とAI回答をMariaDB/MySQL互換DBへ保存
- `FULLTEXT` とLIKEフォールバックによる過去ナレッジ検索
- 画像、動画、音声、ファイル添付の保存とCodexへのローカルパス連携
- Codexが生成した画像・テキスト・PDF等の成果物を公開URL化してLINEへ返却

## 添付ファイルと成果物

LINEの `file` / `image` / `video` / `audio` メッセージを受けると、サーバ側がLINEのcontent取得APIでバイナリを取得し、Web公開root外の `private/attachments` などに保存します。ワーカーは内部APIの認証付きdownload endpointから添付を取得し、Codexプロンプトへ絶対パスとして渡します。

画像添付はCodex CLIの `--image` にも渡します。PDFや通常ファイルはローカル保存パスをプロンプトに渡し、Codex側が必要に応じてファイルを読み取ります。

Codexが処理結果としてファイルを生成する場合、ワーカーは `LINE_AGENT_RESULT_ASSET_OUTPUT_DIR` をジョブごとの成果物出力先としてCodexへ渡します。この配下、または `LINE_AGENT_RESULT_ASSET_ALLOWED_DIRS` で許可したディレクトリ配下の生成ファイルは、内部APIでサーバへアップロードされます。

サーバは生成ファイルを `LINE_AI_AGENT_PUBLIC_ASSET_DIR` 配下へ保存し、HTTPS公開URLを作ってLINEへ返します。JPEG/PNGはLINEの画像メッセージとして送信します。TXT、PDF、Office文書、CSVなど、LINE Messaging APIで任意ファイルとして直接pushできない形式は、LINE本文内のダウンロードURLとして返します。

### 画像・ファイル送信の挙動

AIが生成したファイルは、ローカルパスをLINEへ表示して終わるのではなく、以下の流れでLINEへ返します。

1. ワーカーがCodexへ `LINE_AI_AGENT_RESULT_ASSET_DIR` を環境変数として渡します。
2. Codexがそのディレクトリへ成果物を保存します。
3. ワーカーが成果物を `internal.php` の `result_asset` actionへアップロードします。
4. サーバが `assets/generated/YYYYMMDD/job-{job_id}/` 配下へ保存し、HTTPS URLを発行します。
5. `complete` actionがAI回答本文と成果物を同じLINE pushにまとめて送信します。

画像の送信:

- JPEG/PNGはLINEの画像メッセージとして送信します。
- 1MBを超える画像は、サーバ側でプレビュー画像の生成を試みます。
- GIF/WebPなど、LINE画像メッセージとして直接扱わない形式はダウンロードURLとして返します。

画像以外のファイル送信:

- TXT、Markdown、CSV、TSV、JSON、PDF、DOCX、XLSX、PPTXは既定で成果物として返せます。
- LINE Messaging APIのpushでは任意ファイルを直接添付するmessage typeを前提にしないため、画像以外はHTTPSダウンロードURLとしてLINE本文へ記載します。
- 1回のreply/pushで送れるmessage objectは最大5件です。本文、画像、ファイルリンクが多い場合は、上限内に収まる範囲で送信します。

セキュリティ上の注意:

- 生成成果物のURLはLINEから取得できる公開URLです。機密情報や個人情報を成果物として返す運用は避けてください。
- 実行ファイル、スクリプト、HTML/SVG/XML、アーカイブ、マクロ付きOfficeなどは既定で拒否します。
- 成果物の最大サイズは `LINE_AI_AGENT_RESULT_ASSET_MAX_BYTES` で制限します。

既定では以下を拒否します。

- 実行ファイル、スクリプト、DLL/JAR
- マクロ付きOffice
- zip/rar/7z/tar/gz等のアーカイブ
- 設定上限を超えるファイル

許可拡張子を固定したい場合はサーバ側envの `LINE_AI_AGENT_ATTACHMENT_ALLOWED_EXTENSIONS` を設定します。

生成成果物の許可拡張子を固定したい場合はサーバ側envの `LINE_AI_AGENT_RESULT_ASSET_ALLOWED_EXTENSIONS` を設定します。

## サーバ配置

公開先:

```text
/path/to/webroot/line/
```

非公開設定:

```text
/path/to/private/line-ai-agent.env
```

添付保存:

```text
/path/to/private/attachments/
```

生成成果物公開先:

```text
/path/to/webroot/line/assets/generated/
```

DB初期化:

```bash
mysql -u line_ai_agent -p line_ai_agent < deploy/schema.sql
```

Webhook URL:

```text
https://example.com/line/
```

## サーバ側env

`deploy/line-ai-agent.env.example` を参考に、公開root外へ `line-ai-agent.env` を作成します。実値はGitに入れません。

必要項目:

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_AI_AGENT_DB_HOST`
- `LINE_AI_AGENT_DB_NAME`
- `LINE_AI_AGENT_DB_USER`
- `LINE_AI_AGENT_DB_PASS`
- `LINE_AI_AGENT_WORKER_TOKEN`
- `LINE_AI_AGENT_PUBLIC_BASE_URL`

成果物送信:

- `LINE_AI_AGENT_PUBLIC_BASE_URL`: LINEからアクセスできる公開URLの基点
- `LINE_AI_AGENT_PUBLIC_ASSET_DIR`: 生成成果物を保存する公開ディレクトリ
- `LINE_AI_AGENT_RESULT_ASSET_MAX_BYTES`: 1ファイルあたりの最大サイズ
- `LINE_AI_AGENT_RESULT_ASSET_ALLOWED_EXTENSIONS`: 許可する生成成果物の拡張子
- `LINE_AI_AGENT_RESULT_ASSET_BLOCKED_EXTENSIONS`: 拒否する生成成果物の拡張子

受付返信:

- 通常の短文依頼では、Webhook受信時の受付返信は送らず、処理完了後のAI回答だけをpushします。
- AI回答は、LINEの `quoteToken` が取得できる場合、ユーザーの依頼メッセージへの返信としてpushします。
- 添付、プロジェクト指定、複数行、長文、または `LINE_AI_AGENT_ACK_KEYWORDS` に一致する依頼では `LINE_AI_AGENT_ACK_TEXT` をreplyします。
- 添付単体の依頼では `LINE_AI_AGENT_ATTACHMENT_ACK_TEXT` をreplyします。

## ワーカー設定（Windows例）

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
- `LINE_AGENT_RESULT_ASSET_OUTPUT_DIR`

成果物送信用の任意設定:

- `LINE_AGENT_RESULT_ASSET_ALLOWED_DIRS`: 回答文中のローカルパスから回収してよいディレクトリ。未設定時は `.state/result-assets` とユーザーのCodex生成画像ディレクトリを対象にします。
- `LINE_AGENT_RESULT_ASSET_MAX_COUNT`: 1ジョブでアップロードする成果物数の上限

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

サーバ側は配置後に以下を確認します。PHP CLIのパスは環境に合わせて読み替えてください。

```bash
php -l /path/to/webroot/line/index.php
php -l /path/to/webroot/line/internal.php
curl -sS https://example.com/line/
```

## 公式仕様の前提

- LINE PlatformはWebhook URLへHTTPS POSTします。
- Webhookは `x-line-signature` をraw bodyから検証します。
- 重い処理は非同期化します。
- 添付バイナリはWebhookで受け取るmessage IDから `GET /v2/bot/message/{messageId}/content` で取得します。
- 返信はreply message、処理完了後はpush messageを使います。
- 1回のreply/pushで送れるmessage objectは最大5件です。
- 画像メッセージで直接送れる画像URLはHTTPSのJPEG/PNGです。任意ファイル形式のpush送信用message typeは前提にしません。

参照:

- https://developers.line.biz/en/docs/messaging-api/receiving-messages/
- https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/
- https://developers.line.biz/en/reference/messaging-api/

## ライセンス

MIT Licenseです。商用・非商用を問わず、利用、複製、改変、配布、販売、サブライセンスが可能です。詳細は [LICENSE](LICENSE) を確認してください。
