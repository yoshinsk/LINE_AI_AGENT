<?php
/**
 * <PROJECT_ROOT>\public\line\index.php
 *
 * LINE Developersに設定するWebhook URLです。署名検証、会話保存、ジョブ登録、即時返信を担当します。
 */

declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';

try {
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        line_agent_json_response([
            'ok' => true,
            'service' => 'line-ai-agent',
            'path' => '/line/',
        ]);
    }

    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        line_agent_json_response(['ok' => false, 'error' => 'method_not_allowed'], 405);
    }

    $rawBody = file_get_contents('php://input') ?: '';
    $signature = line_agent_request_header('x-line-signature');
    if (!line_agent_verify_signature($rawBody, (string) $signature)) {
        line_agent_json_response(['ok' => false, 'error' => 'invalid_signature'], 403);
    }

    $payload = json_decode($rawBody, true);
    if (!is_array($payload)) {
        line_agent_json_response(['ok' => false, 'error' => 'invalid_json'], 400);
    }

    $handled = 0;
    foreach (($payload['events'] ?? []) as $event) {
        if (!is_array($event)) {
            continue;
        }

        $sourceInfo = line_agent_normalize_source((array) ($event['source'] ?? []));
        line_agent_upsert_source($sourceInfo);
        $storedEvent = line_agent_store_event($event, $sourceInfo);
        if ($storedEvent['duplicate']) {
            continue;
        }

        $eventType = (string) ($event['type'] ?? '');
        $message = (array) ($event['message'] ?? []);
        $messageType = (string) ($message['type'] ?? '');
        if ($eventType !== 'message') {
            continue;
        }

        if (in_array($messageType, ['file', 'image', 'video', 'audio'], true)) {
            $attachment = line_agent_store_attachment_from_event($event, $sourceInfo, $storedEvent['id']);
            $summary = line_agent_attachment_summary($attachment);
            $messageId = line_agent_store_message(
                $sourceInfo['source_key'],
                'user',
                $summary,
                $event,
                isset($message['id']) ? (string) $message['id'] : null
            );
            line_agent_store_knowledge_chunks($sourceInfo['source_key'], 'user', $summary, $messageId, null, [
                'event_type' => $eventType,
                'line_message_type' => $messageType,
                'attachment_id' => $attachment['id'],
            ]);

            if (!$attachment['stored']) {
                $reply = line_agent_reply($event['replyToken'] ?? null, '添付ファイルを保存できませんでした。状態: ' . $attachment['status']);
                line_agent_store_delivery_attempt(null, $sourceInfo['source_key'], 'reply_attachment_rejected', $reply);
                $handled++;
                continue;
            }

            if ($sourceInfo['source_type'] === 'user') {
                $requestText = "添付ファイルを確認してください。内容を要約し、重要点と次に必要な行動を返信してください。\n" . $summary;
                $jobId = line_agent_enqueue_job($storedEvent['id'], $sourceInfo, $requestText, null);
                line_agent_link_recent_attachments_to_job($sourceInfo['source_key'], $jobId, [(int) $attachment['id']]);
                $reply = line_agent_reply($event['replyToken'] ?? null, line_agent_ack_text('LINE_AI_AGENT_ATTACHMENT_ACK_TEXT', $jobId));
                line_agent_store_delivery_attempt($jobId, $sourceInfo['source_key'], 'reply_attachment_ack', $reply);
                $handled++;
            }
            continue;
        }

        if ($messageType !== 'text') {
            continue;
        }

        $text = trim((string) ($message['text'] ?? ''));
        if ($text === '') {
            continue;
        }

        $messageId = line_agent_store_message(
            $sourceInfo['source_key'],
            'user',
            $text,
            $event,
            isset($message['id']) ? (string) $message['id'] : null
        );
        line_agent_store_knowledge_chunks($sourceInfo['source_key'], 'user', $text, $messageId, null, [
            'event_type' => $eventType,
            'line_message_type' => 'text',
        ]);

        if (!line_agent_is_addressed($sourceInfo, $text, $message)) {
            continue;
        }

        $requestText = line_agent_strip_agent_prefix(line_agent_strip_self_mention_prefix($text, $message));
        if (line_agent_is_status_request($requestText)) {
            $reply = line_agent_reply($event['replyToken'] ?? null, line_agent_format_runtime_status($sourceInfo['source_key']));
            line_agent_store_delivery_attempt(null, $sourceInfo['source_key'], 'reply_status', $reply);
            $handled++;
            continue;
        }

        [$projectRef, $requestText] = line_agent_extract_project_ref($requestText);
        if ($requestText === '') {
            $requestText = $text;
        }
        $ackRequestText = $requestText;
        $quotedContext = line_agent_quoted_agent_message_context($message);
        if ($quotedContext !== null) {
            $requestText = $quotedContext . "\n\n返信内容:\n" . $requestText;
        }
        $jobId = line_agent_enqueue_job($storedEvent['id'], $sourceInfo, $requestText, $projectRef);
        $linkedAttachments = line_agent_link_recent_attachments_to_job($sourceInfo['source_key'], $jobId);
        if (line_agent_should_send_ack($ackRequestText, $projectRef, count($linkedAttachments))) {
            $reply = line_agent_reply($event['replyToken'] ?? null, line_agent_ack_text('LINE_AI_AGENT_ACK_TEXT', $jobId));
            line_agent_store_delivery_attempt($jobId, $sourceInfo['source_key'], 'reply_ack', $reply);
        }
        $handled++;
    }

    line_agent_json_response(['ok' => true, 'handled' => $handled]);
} catch (Throwable $exception) {
    error_log('[line-ai-agent] webhook error: ' . $exception->getMessage());
    line_agent_json_response(['ok' => false, 'error' => 'internal_error'], 500);
}
