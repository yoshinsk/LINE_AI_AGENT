<?php
/**
 * <PROJECT_ROOT>\public\line\internal.php
 *
 * 常駐ワーカーがHTTPS経由でジョブ取得、完了報告、ナレッジ検索、heartbeatを行う内部APIです。
 */

declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';

try {
    line_agent_require_worker_auth();

    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    $body = $method === 'POST' ? line_agent_read_json_body() : [];
    $action = (string) ($_GET['action'] ?? $body['action'] ?? 'health');

    if ($action === 'health') {
        line_agent_db()->query('SELECT 1');
        line_agent_json_response(['ok' => true, 'service' => 'line-ai-agent-internal']);
    }

    if ($action === 'attachment') {
        $attachmentId = (int) ($_GET['id'] ?? $body['id'] ?? 0);
        if ($attachmentId <= 0) {
            line_agent_json_response(['ok' => false, 'error' => 'attachment_id_required'], 400);
        }
        line_agent_attachment_download_response($attachmentId);
    }

    if ($action === 'heartbeat') {
        $workerId = trim((string) ($body['worker_id'] ?? 'unknown-worker'));
        $statusText = mb_substr(trim((string) ($body['status_text'] ?? 'running')), 0, 255, 'UTF-8');
        $metadata = $body['metadata'] ?? [];
        $stmt = line_agent_db()->prepare(
            'INSERT INTO line_worker_heartbeats (worker_id, status_text, metadata_json) VALUES (:worker_id, :status_text, :metadata_json)
             ON DUPLICATE KEY UPDATE status_text = VALUES(status_text), metadata_json = VALUES(metadata_json), last_seen_at = CURRENT_TIMESTAMP'
        );
        $stmt->execute([
            ':worker_id' => $workerId,
            ':status_text' => $statusText,
            ':metadata_json' => json_encode($metadata, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        ]);
        line_agent_json_response(['ok' => true]);
    }

    if ($action === 'claim') {
        line_agent_json_response(line_agent_claim_job($body));
    }

    if ($action === 'complete') {
        line_agent_json_response(line_agent_complete_job($body));
    }

    if ($action === 'result_asset') {
        line_agent_json_response(line_agent_store_result_asset_from_worker($body));
    }

    if ($action === 'knowledge_search') {
        $sourceKey = trim((string) ($body['source_key'] ?? ''));
        $query = trim((string) ($body['query'] ?? ''));
        if ($sourceKey === '' || $query === '') {
            line_agent_json_response(['ok' => false, 'error' => 'source_key_and_query_required'], 400);
        }
        line_agent_json_response([
            'ok' => true,
            'items' => line_agent_search_knowledge($sourceKey, $query, 8),
        ]);
    }

    line_agent_json_response(['ok' => false, 'error' => 'unknown_action'], 404);
} catch (Throwable $exception) {
    error_log('[line-ai-agent] internal api error: ' . $exception->getMessage());
    line_agent_json_response(['ok' => false, 'error' => 'internal_error'], 500);
}

/**
 * queue先頭のジョブを1件だけrunningへ遷移させ、会話履歴と検索ナレッジを付けて返します。
 */
function line_agent_claim_job(array $body): array
{
    $workerId = trim((string) ($body['worker_id'] ?? 'unknown-worker'));
    $leaseSeconds = max(60, min(7200, (int) ($body['lease_seconds'] ?? 1800)));
    $pdo = line_agent_db();

    $pdo->beginTransaction();
    try {
        $pdo->exec("UPDATE line_jobs SET status = 'queued', worker_id = NULL, lease_until = NULL WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < CURRENT_TIMESTAMP()");

        $stmt = $pdo->query("SELECT * FROM line_jobs WHERE status = 'queued' ORDER BY created_at ASC, id ASC LIMIT 1 FOR UPDATE");
        $job = $stmt->fetch();
        if (!$job) {
            $pdo->commit();
            return ['ok' => true, 'job' => null];
        }

        $update = $pdo->prepare(
            "UPDATE line_jobs
                SET status = 'running', worker_id = :worker_id, started_at = COALESCE(started_at, CURRENT_TIMESTAMP()), lease_until = DATE_ADD(CURRENT_TIMESTAMP(), INTERVAL :lease_seconds SECOND)
              WHERE id = :id"
        );
        $update->bindValue(':worker_id', $workerId, PDO::PARAM_STR);
        $update->bindValue(':lease_seconds', $leaseSeconds, PDO::PARAM_INT);
        $update->bindValue(':id', (int) $job['id'], PDO::PARAM_INT);
        $update->execute();
        $pdo->commit();
    } catch (Throwable $exception) {
        $pdo->rollBack();
        throw $exception;
    }

    $job['status'] = 'running';
    $job['worker_id'] = $workerId;
    return [
        'ok' => true,
        'job' => [
            'id' => (int) $job['id'],
            'source_key' => $job['source_key'],
            'source_type' => $job['source_type'],
            'source_external_id' => $job['source_external_id'],
            'request_text' => $job['request_text'],
            'project_ref' => $job['project_ref'],
            'created_at' => $job['created_at'],
        ],
        'recent_messages' => line_agent_recent_messages((string) $job['source_key'], 12),
        'knowledge' => line_agent_search_knowledge((string) $job['source_key'], (string) $job['request_text'], 8),
        'attachments' => line_agent_job_attachments((int) $job['id']),
    ];
}

/**
 * ワーカーの結果をDB保存し、LINE push messageとして会話先へ返します。
 */
function line_agent_complete_job(array $body): array
{
    $jobId = (int) ($body['job_id'] ?? 0);
    $workerId = trim((string) ($body['worker_id'] ?? ''));
    $status = (string) ($body['status'] ?? 'succeeded');
    $resultText = trim((string) ($body['result_text'] ?? ''));
    $errorText = trim((string) ($body['error_text'] ?? ''));
    $assets = is_array($body['assets'] ?? null) ? line_agent_normalize_result_assets($body['assets']) : [];
    if ($jobId <= 0 || !in_array($status, ['succeeded', 'failed'], true)) {
        line_agent_json_response(['ok' => false, 'error' => 'invalid_job_completion'], 400);
    }

    $stmt = line_agent_db()->prepare('SELECT * FROM line_jobs WHERE id = :id');
    $stmt->execute([':id' => $jobId]);
    $job = $stmt->fetch();
    if (!$job) {
        line_agent_json_response(['ok' => false, 'error' => 'job_not_found'], 404);
    }
    if (in_array((string) $job['status'], ['succeeded', 'failed', 'delivery_failed'], true)) {
        return [
            'ok' => true,
            'delivery' => [
                'accepted' => (string) $job['status'] !== 'delivery_failed',
                'already_completed' => true,
            ],
        ];
    }
    if ((string) $job['status'] !== 'running' || $workerId === '' || !hash_equals((string) $job['worker_id'], $workerId)) {
        line_agent_json_response(['ok' => false, 'error' => 'job_not_claimed'], 409);
    }

    $replyText = $resultText !== '' ? $resultText : ($status === 'failed' ? '内部処理を完了できませんでした。' : '処理結果が空でした。');
    $delivery = line_agent_push((string) $job['source_external_id'], $replyText, line_agent_job_quote_token($job), $assets);
    $deliveryAccepted = line_agent_line_delivery_accepted($delivery);
    line_agent_store_delivery_attempt($jobId, (string) $job['source_key'], 'push_result', $delivery);

    $finalStatus = $deliveryAccepted ? $status : 'delivery_failed';
    if (!$deliveryAccepted) {
        $deliveryError = sprintf('LINE push message was not accepted (HTTP %d).', (int) ($delivery['status_code'] ?? 0));
        $errorText = trim($errorText === '' ? $deliveryError : $errorText . "\n" . $deliveryError);
    }
    $update = line_agent_db()->prepare(
        "UPDATE line_jobs
            SET status = :status, result_text = :result_text, error_text = :error_text, finished_at = CURRENT_TIMESTAMP(), lease_until = NULL
          WHERE id = :id"
    );
    $update->execute([
        ':status' => $finalStatus,
        ':result_text' => $resultText,
        ':error_text' => $errorText,
        ':id' => $jobId,
    ]);

    if ($deliveryAccepted) {
        $messageId = line_agent_store_message((string) $job['source_key'], 'assistant', $replyText, null, null);
        line_agent_store_knowledge_chunks((string) $job['source_key'], 'assistant', $replyText, $messageId, $jobId, [
            'job_status' => $status,
        ]);
    }

    line_agent_json_response([
        'ok' => true,
        'delivery' => [
            'status_code' => $delivery['status_code'],
            'accepted' => $deliveryAccepted,
            'attempt_count' => $delivery['attempt_count'] ?? 1,
        ],
    ]);
}
