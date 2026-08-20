<?php
/**
 * <PROJECT_ROOT>\public\line\bootstrap.php
 *
 * LINE Webhook入口とWindowsワーカー内部APIで共有する設定読込、DB接続、LINE送信、会話保存、検索処理です。
 */

declare(strict_types=1);

const LINE_AGENT_DEFAULT_ENV_FILE = __DIR__ . '/../../private/line-ai-agent.env';
const LINE_AGENT_MAX_LINE_TEXT_CHARS = 4500;
const LINE_AGENT_DEFAULT_ATTACHMENT_DIR = __DIR__ . '/../../private/attachments';
const LINE_AGENT_DEFAULT_ACK_TEXT = '改めて返信します。少々お待ちください。';

/**
 * KEY=VALUE形式の設定ファイルを読み込みます。値の引用符は最外周のみ外します。
 */
function line_agent_load_env_file(string $path): array
{
    if (!is_file($path)) {
        return [];
    }

    $values = [];
    $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines ?: [] as $rawLine) {
        $line = trim($rawLine);
        if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) {
            continue;
        }
        [$key, $value] = explode('=', $line, 2);
        $key = trim($key);
        $value = trim($value);
        if ($key === '') {
            continue;
        }
        if (
            strlen($value) >= 2
            && (($value[0] === '"' && substr($value, -1) === '"') || ($value[0] === "'" && substr($value, -1) === "'"))
        ) {
            $value = substr($value, 1, -1);
        }
        $values[$key] = $value;
    }
    return $values;
}

/**
 * 環境変数と非公開envファイルを統合し、プロセス環境変数を優先します。
 */
function line_agent_config(?string $key = null, ?string $default = null): mixed
{
    static $config = null;

    if ($config === null) {
        $envFile = getenv('LINE_AI_AGENT_ENV') ?: LINE_AGENT_DEFAULT_ENV_FILE;
        $config = line_agent_load_env_file($envFile);
        foreach ($_ENV as $envKey => $envValue) {
            $config[$envKey] = (string) $envValue;
        }
        foreach ($_SERVER as $serverKey => $serverValue) {
            if (is_string($serverValue) && preg_match('/^[A-Z0-9_]+$/', $serverKey)) {
                $config[$serverKey] = $serverValue;
            }
        }
    }

    if ($key === null) {
        return $config;
    }
    $envValue = getenv($key);
    if ($envValue !== false) {
        return $envValue;
    }
    return $config[$key] ?? $default;
}

/**
 * 必須設定を取得します。欠落時は運用ミスとして例外にします。
 */
function line_agent_required_config(string $key): string
{
    $value = trim((string) line_agent_config($key, ''));
    if ($value === '') {
        throw new RuntimeException($key . ' is required');
    }
    return $value;
}

/**
 * MariaDBへPDOで接続します。全テーブルはutf8mb4前提です。
 */
function line_agent_db(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }

    $host = line_agent_config('LINE_AI_AGENT_DB_HOST', 'localhost');
    $dbName = line_agent_required_config('LINE_AI_AGENT_DB_NAME');
    $user = line_agent_required_config('LINE_AI_AGENT_DB_USER');
    $password = line_agent_required_config('LINE_AI_AGENT_DB_PASS');
    $dsn = sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $host, $dbName);
    $pdo = new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    return $pdo;
}

/**
 * JSONを返して処理を終了します。
 */
function line_agent_json_response(array $payload, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

/**
 * HTTPヘッダ名を大文字小文字非依存で取得します。Webサーバ差異をここで吸収します。
 */
function line_agent_request_header(string $name): string
{
    $target = strtolower($name);
    $headers = function_exists('getallheaders') ? getallheaders() : [];
    foreach ($headers ?: [] as $headerName => $headerValue) {
        if (strtolower((string) $headerName) === $target) {
            return is_array($headerValue) ? implode(',', $headerValue) : (string) $headerValue;
        }
    }

    $serverKey = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return isset($_SERVER[$serverKey]) ? (string) $_SERVER[$serverKey] : '';
}

/**
 * Webhook署名をLINE公式仕様に従ってraw bodyから検証します。
 */
function line_agent_verify_signature(string $rawBody, string $signature): bool
{
    $secret = line_agent_required_config('LINE_CHANNEL_SECRET');
    $digest = base64_encode(hash_hmac('sha256', $rawBody, $secret, true));
    return hash_equals($digest, $signature);
}

/**
 * LINEのsourceオブジェクトをDB用の安定キーへ正規化します。
 */
function line_agent_normalize_source(array $source): array
{
    $type = (string) ($source['type'] ?? 'unknown');
    $externalId = match ($type) {
        'user' => (string) ($source['userId'] ?? ''),
        'group' => (string) ($source['groupId'] ?? ''),
        'room' => (string) ($source['roomId'] ?? ''),
        default => (string) ($source['userId'] ?? $source['groupId'] ?? $source['roomId'] ?? 'unknown'),
    };
    if ($externalId === '') {
        $externalId = 'unknown';
    }
    return [
        'source_type' => $type,
        'source_external_id' => $externalId,
        'source_key' => $type . ':' . $externalId,
    ];
}

/**
 * sourceを登録または更新します。後続の会話履歴とジョブが参照する基準データです。
 */
function line_agent_upsert_source(array $sourceInfo): void
{
    $sql = <<<SQL
INSERT INTO line_sources (source_key, source_type, source_external_id)
VALUES (:source_key, :source_type, :source_external_id)
ON DUPLICATE KEY UPDATE
  source_type = VALUES(source_type),
  source_external_id = VALUES(source_external_id),
  updated_at = CURRENT_TIMESTAMP
SQL;
    $stmt = line_agent_db()->prepare($sql);
    $stmt->execute([
        ':source_key' => $sourceInfo['source_key'],
        ':source_type' => $sourceInfo['source_type'],
        ':source_external_id' => $sourceInfo['source_external_id'],
    ]);
}

/**
 * Webhookイベントを保存します。重複イベントはduplicate=trueとして返します。
 */
function line_agent_store_event(array $event, array $sourceInfo): array
{
    $raw = json_encode($event, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    $hash = hash('sha256', (string) $raw);
    $webhookEventId = $event['webhookEventId'] ?? null;
    $sql = <<<SQL
INSERT INTO line_webhook_events (webhook_event_id, event_hash, source_key, event_type, raw_json)
VALUES (:webhook_event_id, :event_hash, :source_key, :event_type, :raw_json)
SQL;
    try {
        $stmt = line_agent_db()->prepare($sql);
        $stmt->execute([
            ':webhook_event_id' => $webhookEventId,
            ':event_hash' => $hash,
            ':source_key' => $sourceInfo['source_key'],
            ':event_type' => (string) ($event['type'] ?? 'unknown'),
            ':raw_json' => $raw,
        ]);
        return ['id' => (int) line_agent_db()->lastInsertId(), 'duplicate' => false];
    } catch (PDOException $exception) {
        if ($exception->getCode() === '23000') {
            return ['id' => null, 'duplicate' => true];
        }
        throw $exception;
    }
}

/**
 * LINE上の発話またはAI回答を会話履歴として保存します。
 */
function line_agent_store_message(string $sourceKey, string $role, string $body, ?array $raw = null, ?string $lineMessageId = null): int
{
    $stmt = line_agent_db()->prepare(
        'INSERT INTO line_messages (source_key, role, line_message_id, body, raw_json) VALUES (:source_key, :role, :line_message_id, :body, :raw_json)'
    );
    $stmt->execute([
        ':source_key' => $sourceKey,
        ':role' => $role,
        ':line_message_id' => $lineMessageId,
        ':body' => $body,
        ':raw_json' => $raw === null ? null : json_encode($raw, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
    ]);
    return (int) line_agent_db()->lastInsertId();
}

/**
 * 検索再利用しやすい単位へ分割してナレッジテーブルへ保存します。
 */
function line_agent_store_knowledge_chunks(
    string $sourceKey,
    string $role,
    string $text,
    ?int $messageId = null,
    ?int $jobId = null,
    array $metadata = []
): void {
    $cleanText = trim(preg_replace('/\s+/u', ' ', $text) ?? $text);
    if ($cleanText === '') {
        return;
    }
    $chunks = line_agent_chunk_text($cleanText, 1800);
    $stmt = line_agent_db()->prepare(
        'INSERT INTO line_knowledge_chunks (source_key, message_id, job_id, role, text, metadata_json) VALUES (:source_key, :message_id, :job_id, :role, :text, :metadata_json)'
    );
    foreach ($chunks as $index => $chunk) {
        $metadata['chunk_index'] = $index;
        $stmt->execute([
            ':source_key' => $sourceKey,
            ':message_id' => $messageId,
            ':job_id' => $jobId,
            ':role' => $role,
            ':text' => $chunk,
            ':metadata_json' => json_encode($metadata, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        ]);
    }
}

/**
 * マルチバイト文字列を最大長で分割します。LINE送信と検索チャンク化の共通処理です。
 */
function line_agent_chunk_text(string $text, int $maxChars): array
{
    $text = trim($text);
    if ($text === '') {
        return [];
    }
    $chunks = [];
    while (mb_strlen($text, 'UTF-8') > $maxChars) {
        $chunk = mb_substr($text, 0, $maxChars, 'UTF-8');
        $breakAt = max(mb_strrpos($chunk, "\n", 0, 'UTF-8') ?: 0, mb_strrpos($chunk, '。', 0, 'UTF-8') ?: 0);
        if ($breakAt > 500) {
            $chunk = mb_substr($chunk, 0, $breakAt + 1, 'UTF-8');
        }
        $chunks[] = trim($chunk);
        $text = trim(mb_substr($text, mb_strlen($chunk, 'UTF-8'), null, 'UTF-8'));
    }
    if ($text !== '') {
        $chunks[] = $text;
    }
    return $chunks;
}

/**
 * LINE text message配列を作ります。1回のreply/push上限に合わせて最大5件へ制限します。
 */
function line_agent_text_messages(string $text): array
{
    $chunks = array_slice(line_agent_chunk_text($text, LINE_AGENT_MAX_LINE_TEXT_CHARS), 0, 5);
    if (!$chunks) {
        $chunks = ['処理結果が空でした。'];
    }
    return array_map(fn (string $chunk): array => ['type' => 'text', 'text' => $chunk], $chunks);
}

/**
 * LINE Messaging APIへJSON POSTします。結果は配信試行ログ用に返します。
 */
function line_agent_line_api_post(string $path, array $payload): array
{
    $token = line_agent_required_config('LINE_CHANNEL_ACCESS_TOKEN');
    $url = 'https://api.line.me' . $path;
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    $headers = [
        'Content-Type: application/json',
        'Authorization: Bearer ' . $token,
    ];
    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => implode("\r\n", $headers),
            'content' => $json,
            'ignore_errors' => true,
            'timeout' => 20,
        ],
    ]);
    $body = file_get_contents($url, false, $context);
    $statusCode = 0;
    foreach ($http_response_header ?? [] as $header) {
        if (preg_match('/^HTTP\/\S+\s+(\d+)/', $header, $matches)) {
            $statusCode = (int) $matches[1];
            break;
        }
    }
    return ['status_code' => $statusCode, 'body' => (string) $body];
}

/**
 * LINEのcontent取得APIからバイナリを取得します。添付保存の入口です。
 */
function line_agent_line_api_get_content(string $messageId): array
{
    $token = line_agent_required_config('LINE_CHANNEL_ACCESS_TOKEN');
    $url = 'https://api-data.line.me/v2/bot/message/' . rawurlencode($messageId) . '/content';
    $context = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => 'Authorization: Bearer ' . $token,
            'ignore_errors' => true,
            'timeout' => 60,
        ],
    ]);
    $body = file_get_contents($url, false, $context);
    $statusCode = 0;
    $contentType = null;
    foreach ($http_response_header ?? [] as $header) {
        if (preg_match('/^HTTP\/\S+\s+(\d+)/', $header, $matches)) {
            $statusCode = (int) $matches[1];
        } elseif (stripos($header, 'Content-Type:') === 0) {
            $contentType = trim(substr($header, strlen('Content-Type:')));
        }
    }
    return [
        'status_code' => $statusCode,
        'content_type' => $contentType,
        'body' => $body === false ? '' : $body,
    ];
}

/**
 * replyTokenで即時返信します。replyTokenが無いイベントでは何もしません。
 */
function line_agent_reply(?string $replyToken, string $text): ?array
{
    if (!$replyToken) {
        return null;
    }
    return line_agent_line_api_post('/v2/bot/message/reply', [
        'replyToken' => $replyToken,
        'messages' => line_agent_text_messages($text),
    ]);
}

/**
 * 処理完了後にpush messageで会話先へ結果を返します。
 */
function line_agent_push(string $to, string $text): array
{
    return line_agent_line_api_post('/v2/bot/message/push', [
        'to' => $to,
        'messages' => line_agent_text_messages($text),
    ]);
}

/**
 * 添付保存ディレクトリを公開root外の絶対パスへ解決します。
 */
function line_agent_attachment_dir(): string
{
    $configured = trim((string) line_agent_config('LINE_AI_AGENT_ATTACHMENT_DIR', LINE_AGENT_DEFAULT_ATTACHMENT_DIR));
    if ($configured === '') {
        $configured = LINE_AGENT_DEFAULT_ATTACHMENT_DIR;
    }
    if (preg_match('/^[A-Za-z]:[\\\\\/]/', $configured) || str_starts_with($configured, '/')) {
        return rtrim($configured, "/\\");
    }
    return rtrim(__DIR__ . '/../../' . $configured, "/\\");
}

/**
 * ファイル名を保存・送信用に安全化します。
 */
function line_agent_safe_file_name(string $fileName, string $fallback): string
{
    $base = trim(str_replace(["\0", '/', '\\'], '_', $fileName));
    $base = preg_replace('/[^\pL\pN._ -]+/u', '_', $base) ?: '';
    $base = trim($base, " .\t\n\r\0\x0B");
    if ($base === '') {
        $base = $fallback;
    }
    return mb_substr($base, 0, 180, 'UTF-8');
}

/**
 * LINEメッセージ種別から既定のファイル名を決めます。
 */
function line_agent_default_attachment_name(string $messageType, string $messageId, array $message): string
{
    if ($messageType === 'file') {
        return line_agent_safe_file_name((string) ($message['fileName'] ?? ''), 'line-file-' . $messageId);
    }
    $extension = match ($messageType) {
        'image' => 'jpg',
        'video' => 'mp4',
        'audio' => 'm4a',
        default => 'bin',
    };
    return 'line-' . $messageType . '-' . $messageId . '.' . $extension;
}

/**
 * 添付拡張子が運用ポリシー上安全かを判定します。
 */
function line_agent_attachment_allowed(string $fileName): bool
{
    $extension = strtolower(pathinfo($fileName, PATHINFO_EXTENSION));
    $blocked = array_filter(array_map('trim', explode(',', (string) line_agent_config(
        'LINE_AI_AGENT_ATTACHMENT_BLOCKED_EXTENSIONS',
        'exe,bat,cmd,com,msi,ps1,sh,bash,zsh,fish,js,vbs,wsf,hta,php,py,rb,pl,jar,dll,so,dylib,docm,xlsm,pptm,zip,rar,7z,tar,gz,bz2,xz'
    ))));
    if ($extension !== '' && in_array($extension, $blocked, true)) {
        return false;
    }

    $allowedRaw = trim((string) line_agent_config('LINE_AI_AGENT_ATTACHMENT_ALLOWED_EXTENSIONS', ''));
    if ($allowedRaw === '') {
        return true;
    }
    $allowed = array_filter(array_map('trim', explode(',', strtolower($allowedRaw))));
    return $extension !== '' && in_array($extension, $allowed, true);
}

/**
 * LINE添付イベントを取得・保存し、DBにメタデータを残します。
 */
function line_agent_store_attachment_from_event(array $event, array $sourceInfo, ?int $eventId): array
{
    $message = (array) ($event['message'] ?? []);
    $messageType = (string) ($message['type'] ?? '');
    $lineMessageId = (string) ($message['id'] ?? '');
    if ($lineMessageId === '' || !in_array($messageType, ['file', 'image', 'video', 'audio'], true)) {
        return ['stored' => false, 'id' => null, 'status' => 'unsupported'];
    }

    $fileName = line_agent_default_attachment_name($messageType, $lineMessageId, $message);
    $fileName = line_agent_safe_file_name($fileName, 'line-' . $messageType . '-' . $lineMessageId);
    $declaredSize = isset($message['fileSize']) ? (int) $message['fileSize'] : null;
    $maxBytes = max(1, (int) line_agent_config('LINE_AI_AGENT_ATTACHMENT_MAX_BYTES', (string) (25 * 1024 * 1024)));
    if ($declaredSize !== null && $declaredSize > $maxBytes) {
        return line_agent_insert_attachment_row($eventId, $sourceInfo, $lineMessageId, $messageType, $fileName, $declaredSize, null, null, null, 'rejected_size', $message);
    }
    if (!line_agent_attachment_allowed($fileName)) {
        return line_agent_insert_attachment_row($eventId, $sourceInfo, $lineMessageId, $messageType, $fileName, $declaredSize, null, null, null, 'rejected_extension', $message);
    }

    $providerType = (string) (($message['contentProvider'] ?? [])['type'] ?? 'line');
    if ($providerType === 'external') {
        return line_agent_insert_attachment_row(
            $eventId,
            $sourceInfo,
            $lineMessageId,
            $messageType,
            $fileName,
            $declaredSize,
            null,
            null,
            null,
            'external',
            $message
        );
    }

    $download = line_agent_line_api_get_content($lineMessageId);
    if ($download['status_code'] < 200 || $download['status_code'] >= 300 || $download['body'] === '') {
        return line_agent_insert_attachment_row($eventId, $sourceInfo, $lineMessageId, $messageType, $fileName, $declaredSize, $download['content_type'], null, null, 'download_failed', $message);
    }

    $binary = (string) $download['body'];
    $actualSize = strlen($binary);
    if ($actualSize > $maxBytes) {
        return line_agent_insert_attachment_row($eventId, $sourceInfo, $lineMessageId, $messageType, $fileName, $actualSize, $download['content_type'], null, null, 'rejected_size', $message);
    }

    $sourceHash = substr(hash('sha256', $sourceInfo['source_key']), 0, 16);
    $targetDir = line_agent_attachment_dir() . '/' . date('Ymd') . '/' . $sourceHash;
    if (!is_dir($targetDir) && !mkdir($targetDir, 0700, true) && !is_dir($targetDir)) {
        throw new RuntimeException('attachment directory could not be created');
    }
    $targetPath = $targetDir . '/' . line_agent_safe_file_name($lineMessageId . '-' . $fileName, 'attachment-' . $lineMessageId);
    file_put_contents($targetPath, $binary, LOCK_EX);
    @chmod($targetPath, 0600);

    return line_agent_insert_attachment_row(
        $eventId,
        $sourceInfo,
        $lineMessageId,
        $messageType,
        $fileName,
        $actualSize,
        $download['content_type'],
        hash('sha256', $binary),
        $targetPath,
        'stored',
        $message
    );
}

/**
 * 添付メタデータ行を作成します。重複イベント時は既存行を返します。
 */
function line_agent_insert_attachment_row(
    ?int $eventId,
    array $sourceInfo,
    string $lineMessageId,
    string $messageType,
    string $fileName,
    ?int $fileSize,
    ?string $contentType,
    ?string $sha256,
    ?string $storagePath,
    string $status,
    array $metadata
): array {
    $stmt = line_agent_db()->prepare(
        'INSERT INTO line_attachments (event_id, source_key, line_message_id, message_type, original_file_name, file_size, content_type, sha256, storage_path, storage_status, metadata_json)
         VALUES (:event_id, :source_key, :line_message_id, :message_type, :original_file_name, :file_size, :content_type, :sha256, :storage_path, :storage_status, :metadata_json)'
    );
    try {
        $stmt->execute([
            ':event_id' => $eventId,
            ':source_key' => $sourceInfo['source_key'],
            ':line_message_id' => $lineMessageId,
            ':message_type' => $messageType,
            ':original_file_name' => $fileName,
            ':file_size' => $fileSize,
            ':content_type' => $contentType,
            ':sha256' => $sha256,
            ':storage_path' => $storagePath,
            ':storage_status' => $status,
            ':metadata_json' => json_encode($metadata, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        ]);
        return [
            'stored' => $status === 'stored' || $status === 'external',
            'id' => (int) line_agent_db()->lastInsertId(),
            'status' => $status,
            'file_name' => $fileName,
            'file_size' => $fileSize,
        ];
    } catch (PDOException $exception) {
        if ($exception->getCode() !== '23000') {
            throw $exception;
        }
        $existing = line_agent_db()->prepare('SELECT id, storage_status, original_file_name, file_size FROM line_attachments WHERE line_message_id = :line_message_id');
        $existing->execute([':line_message_id' => $lineMessageId]);
        $row = $existing->fetch();
        return [
            'stored' => in_array((string) ($row['storage_status'] ?? ''), ['stored', 'external'], true),
            'id' => isset($row['id']) ? (int) $row['id'] : null,
            'status' => (string) ($row['storage_status'] ?? 'duplicate'),
            'file_name' => (string) ($row['original_file_name'] ?? $fileName),
            'file_size' => isset($row['file_size']) ? (int) $row['file_size'] : $fileSize,
        ];
    }
}

/**
 * 添付の保存結果をLINE会話履歴・ナレッジへ残す本文に整形します。
 */
function line_agent_attachment_summary(array $attachment): string
{
    $size = isset($attachment['file_size']) && $attachment['file_size'] !== null
        ? ' ' . number_format((int) $attachment['file_size']) . ' bytes'
        : '';
    return sprintf('[添付:%s] %s%s', (string) $attachment['status'], (string) ($attachment['file_name'] ?? 'unknown'), $size);
}

/**
 * 直近の未使用添付をジョブへ紐づけます。明示IDがあればそれだけを対象にします。
 */
function line_agent_link_recent_attachments_to_job(string $sourceKey, int $jobId, array $attachmentIds = []): array
{
    if ($attachmentIds) {
        $placeholders = implode(',', array_fill(0, count($attachmentIds), '?'));
        $params = array_merge([$jobId, $sourceKey], array_map('intval', $attachmentIds));
        $stmt = line_agent_db()->prepare(
            "UPDATE line_attachments SET job_id = ? WHERE source_key = ? AND id IN ($placeholders) AND storage_status IN ('stored', 'external')"
        );
        $stmt->execute($params);
        return line_agent_job_attachments($jobId);
    }

    $minutes = max(1, min(1440, (int) line_agent_config('LINE_AI_AGENT_ATTACHMENT_RECENT_MINUTES', '30')));
    $limit = max(1, min(10, (int) line_agent_config('LINE_AI_AGENT_ATTACHMENT_MAX_PER_JOB', '5')));
    $stmt = line_agent_db()->prepare(
        "SELECT id FROM line_attachments
          WHERE source_key = :source_key
            AND job_id IS NULL
            AND storage_status IN ('stored', 'external')
            AND created_at >= DATE_SUB(CURRENT_TIMESTAMP, INTERVAL $minutes MINUTE)
          ORDER BY created_at DESC
          LIMIT $limit"
    );
    $stmt->execute([':source_key' => $sourceKey]);
    $ids = array_map('intval', array_column($stmt->fetchAll(), 'id'));
    if (!$ids) {
        return [];
    }
    return line_agent_link_recent_attachments_to_job($sourceKey, $jobId, $ids);
}

/**
 * ジョブに紐づく添付をワーカーへ渡すメタデータに整形します。
 */
function line_agent_job_attachments(int $jobId): array
{
    $stmt = line_agent_db()->prepare(
        "SELECT id, message_type, original_file_name, file_size, content_type, sha256, storage_status, metadata_json, created_at
           FROM line_attachments
          WHERE job_id = :job_id
          ORDER BY created_at ASC"
    );
    $stmt->execute([':job_id' => $jobId]);
    $items = [];
    foreach ($stmt->fetchAll() as $row) {
        $metadata = json_decode((string) ($row['metadata_json'] ?? ''), true);
        $items[] = [
            'id' => (int) $row['id'],
            'message_type' => (string) $row['message_type'],
            'file_name' => (string) $row['original_file_name'],
            'file_size' => isset($row['file_size']) ? (int) $row['file_size'] : null,
            'content_type' => $row['content_type'],
            'sha256' => $row['sha256'],
            'storage_status' => (string) $row['storage_status'],
            'metadata' => is_array($metadata) ? $metadata : [],
        ];
    }
    return $items;
}

/**
 * 認証済み内部APIから添付バイナリを返します。Windowsワーカーのダウンロード専用です。
 */
function line_agent_attachment_download_response(int $attachmentId): never
{
    $stmt = line_agent_db()->prepare(
        "SELECT original_file_name, content_type, storage_path, storage_status FROM line_attachments WHERE id = :id"
    );
    $stmt->execute([':id' => $attachmentId]);
    $row = $stmt->fetch();
    if (!$row || $row['storage_status'] !== 'stored' || !is_file((string) $row['storage_path'])) {
        line_agent_json_response(['ok' => false, 'error' => 'attachment_not_found'], 404);
    }

    header('Content-Type: ' . (($row['content_type'] ?: 'application/octet-stream')));
    header('Content-Disposition: attachment; filename="' . addcslashes((string) $row['original_file_name'], "\"\\") . '"');
    header('Content-Length: ' . filesize((string) $row['storage_path']));
    readfile((string) $row['storage_path']);
    exit;
}

/**
 * プロジェクト指定行を取り出し、AIへ渡す本文からは削除します。
 */
function line_agent_extract_project_ref(string $text): array
{
    $projectRef = null;
    $clean = preg_replace_callback('/^\s*(?:project|プロジェクト)\s*[:：]\s*(.+?)\s*$/imu', function (array $matches) use (&$projectRef): string {
        $projectRef = trim($matches[1]);
        return '';
    }, $text);
    return [$projectRef, trim((string) $clean)];
}

/**
 * LINEの会話先種別ごとに、AIが反応すべき明示呼び出しかを判定します。
 */
function line_agent_is_addressed(array $sourceInfo, string $text): bool
{
    if ($sourceInfo['source_type'] === 'user') {
        return true;
    }
    $trimmed = trim($text);
    if (preg_match('/^(@?AI|@?ai|ＡＩ|ａｉ)\s*[:：　 ]/u', $trimmed)) {
        return true;
    }
    if (preg_match('/^\s*(?:project|プロジェクト)\s*[:：]/imu', $trimmed)) {
        return true;
    }
    return str_contains($trimmed, 'プロジェクト一覧') || str_contains(strtolower($trimmed), 'project list');
}

/**
 * グループ明示呼び出しの接頭辞だけを取り除きます。
 */
function line_agent_strip_agent_prefix(string $text): string
{
    return trim((string) preg_replace('/^(@?AI|@?ai|ＡＩ|ａｉ)\s*[:：　 ]/u', '', trim($text)));
}

/**
 * 時間がかかる可能性のある依頼だけ、LINEへ受付返信を出すか判定します。
 */
function line_agent_should_send_ack(string $requestText, ?string $projectRef = null, int $attachmentCount = 0): bool
{
    if ($attachmentCount > 0 || trim((string) $projectRef) !== '') {
        return true;
    }

    $text = trim($requestText);
    if ($text === '') {
        return false;
    }
    if (str_contains($text, "\n") || str_contains($text, "\r")) {
        return true;
    }

    $minChars = max(1, (int) line_agent_config('LINE_AI_AGENT_ACK_MIN_CHARS', '80'));
    if (mb_strlen($text, 'UTF-8') >= $minChars) {
        return true;
    }

    $markers = array_filter(array_map('trim', explode(',', (string) line_agent_config(
        'LINE_AI_AGENT_ACK_KEYWORDS',
        '実装,修正,変更,調査,確認,解析,分析,要約,作成,生成,添付,ファイル,画像,ログ,エラー,テスト実行,テストを実行,デプロイ,配置,DB,データベース,commit,push,build,debug,review,tests'
    ))));
    foreach ($markers as $marker) {
        if ($marker !== '' && mb_stripos($text, $marker, 0, 'UTF-8') !== false) {
            return true;
        }
    }
    return false;
}

/**
 * 受付返信文を設定から取得します。旧設定の{job_id}は互換目的で置換します。
 */
function line_agent_ack_text(string $configKey, int $jobId): string
{
    $text = (string) line_agent_config($configKey, LINE_AGENT_DEFAULT_ACK_TEXT);
    $text = str_replace('\\n', "\n", $text);
    return str_replace('{job_id}', (string) $jobId, $text);
}

/**
 * 状態確認だけで処理すべき短文かを判定します。
 */
function line_agent_is_status_request(string $text): bool
{
    $trimmed = trim($text);
    if (mb_strlen($trimmed, 'UTF-8') > 80) {
        return false;
    }
    foreach (['現状', '進捗', '状況', 'ステータス', 'どこまで', '返事がありません', '返信がありません', 'status'] as $marker) {
        if (stripos($trimmed, $marker) !== false) {
            return true;
        }
    }
    return false;
}

/**
 * 同一会話先の未完了ジョブだけをLINE向けに整形します。
 */
function line_agent_format_runtime_status(string $sourceKey): string
{
    $stmt = line_agent_db()->prepare(
        "SELECT id, status, request_text, created_at, started_at FROM line_jobs WHERE source_key = :source_key AND status IN ('queued', 'running') ORDER BY created_at ASC LIMIT 10"
    );
    $stmt->execute([':source_key' => $sourceKey]);
    $jobs = $stmt->fetchAll();
    if (!$jobs) {
        return '現在、未完了の作業はありません。';
    }

    $lines = ['未完了の作業:'];
    foreach ($jobs as $job) {
        $preview = mb_substr(trim((string) $job['request_text']), 0, 80, 'UTF-8');
        $lines[] = sprintf('#%s %s %s', $job['id'], $job['status'], $preview);
    }
    return implode("\n", $lines);
}

/**
 * Codex実行が必要な依頼をDBキューへ登録します。
 */
function line_agent_enqueue_job(?int $eventId, array $sourceInfo, string $requestText, ?string $projectRef): int
{
    $stmt = line_agent_db()->prepare(
        'INSERT INTO line_jobs (event_id, source_key, source_type, source_external_id, request_text, project_ref) VALUES (:event_id, :source_key, :source_type, :source_external_id, :request_text, :project_ref)'
    );
    $stmt->execute([
        ':event_id' => $eventId,
        ':source_key' => $sourceInfo['source_key'],
        ':source_type' => $sourceInfo['source_type'],
        ':source_external_id' => $sourceInfo['source_external_id'],
        ':request_text' => $requestText,
        ':project_ref' => $projectRef,
    ]);
    return (int) line_agent_db()->lastInsertId();
}

/**
 * 最近の会話履歴をワーカーのプロンプト文脈として取得します。
 */
function line_agent_recent_messages(string $sourceKey, int $limit = 12): array
{
    $stmt = line_agent_db()->prepare(
        'SELECT role, body, created_at FROM line_messages WHERE source_key = :source_key ORDER BY created_at DESC LIMIT :limit'
    );
    $stmt->bindValue(':source_key', $sourceKey, PDO::PARAM_STR);
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
    $stmt->execute();
    return array_reverse($stmt->fetchAll());
}

/**
 * FULLTEXTを優先し、失敗時はLIKE検索でナレッジ候補を返します。
 */
function line_agent_search_knowledge(string $sourceKey, string $query, int $limit = 8): array
{
    $query = trim($query);
    if ($query === '') {
        return [];
    }
    try {
        $stmt = line_agent_db()->prepare(
            'SELECT role, text, created_at, MATCH(text) AGAINST(:query IN NATURAL LANGUAGE MODE) AS score
               FROM line_knowledge_chunks
              WHERE source_key = :source_key AND MATCH(text) AGAINST(:query IN NATURAL LANGUAGE MODE)
              ORDER BY score DESC, created_at DESC
              LIMIT :limit'
        );
        $stmt->bindValue(':source_key', $sourceKey, PDO::PARAM_STR);
        $stmt->bindValue(':query', $query, PDO::PARAM_STR);
        $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
        if ($rows) {
            return $rows;
        }
    } catch (Throwable $ignored) {
        // FULLTEXT設定差異に備え、LIKE検索へ落とします。
    }

    $tokens = preg_split('/[\s　]+/u', $query) ?: [];
    $tokens = array_values(array_filter(array_map('trim', $tokens), fn (string $token): bool => $token !== ''));
    if (!$tokens) {
        $tokens = [mb_substr($query, 0, 40, 'UTF-8')];
    }
    $where = ['source_key = :source_key'];
    $params = [':source_key' => $sourceKey];
    foreach (array_slice($tokens, 0, 5) as $index => $token) {
        $key = ':q' . $index;
        $where[] = 'text LIKE ' . $key;
        $params[$key] = '%' . $token . '%';
    }
    $sql = 'SELECT role, text, created_at, 0 AS score FROM line_knowledge_chunks WHERE ' . implode(' AND ', $where)
        . ' ORDER BY created_at DESC LIMIT ' . max(1, min(20, $limit));
    $stmt = line_agent_db()->prepare($sql);
    $stmt->execute($params);
    return $stmt->fetchAll();
}

/**
 * LINE配信結果を保存します。API失敗時の切り分けに使います。
 */
function line_agent_store_delivery_attempt(?int $jobId, string $sourceKey, string $deliveryType, ?array $result): void
{
    if ($result === null) {
        return;
    }
    $stmt = line_agent_db()->prepare(
        'INSERT INTO line_delivery_attempts (job_id, source_key, delivery_type, status_code, response_body) VALUES (:job_id, :source_key, :delivery_type, :status_code, :response_body)'
    );
    $stmt->execute([
        ':job_id' => $jobId,
        ':source_key' => $sourceKey,
        ':delivery_type' => $deliveryType,
        ':status_code' => $result['status_code'] ?? null,
        ':response_body' => $result['body'] ?? null,
    ]);
}

/**
 * 内部APIトークンを検証します。Windowsワーカーだけが通る境界です。
 */
function line_agent_require_worker_auth(): void
{
    $expected = line_agent_required_config('LINE_AI_AGENT_WORKER_TOKEN');
    $provided = line_agent_request_header('X-Line-AI-Agent-Worker-Token');
    $auth = line_agent_request_header('Authorization');
    if ($provided === '' && str_starts_with($auth, 'Bearer ')) {
        $provided = substr($auth, 7);
    }
    if (!is_string($provided) || !hash_equals($expected, $provided)) {
        line_agent_json_response(['ok' => false, 'error' => 'unauthorized'], 401);
    }
}

/**
 * JSONリクエストボディを配列として取得します。
 */
function line_agent_read_json_body(): array
{
    $raw = file_get_contents('php://input') ?: '';
    if ($raw === '') {
        return [];
    }
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) {
        line_agent_json_response(['ok' => false, 'error' => 'invalid_json'], 400);
    }
    return $decoded;
}
