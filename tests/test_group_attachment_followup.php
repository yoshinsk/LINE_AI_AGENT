<?php
/**
 * <PROJECT_ROOT>/tests/test_group_attachment_followup.php
 *
 * グループ内で直近添付への作業指示をメンションなしで判定する、純粋な文言判定を検証します。
 */

declare(strict_types=1);

require_once __DIR__ . '/../public/line/bootstrap.php';

/**
 * 判定結果が期待値と異なる場合に、対象文言を含めてテストを停止します。
 */
function assert_attachment_instruction(string $text, bool $expected): void
{
    $actual = line_agent_is_attachment_work_instruction($text);
    if ($actual !== $expected) {
        throw new RuntimeException(sprintf('unexpected attachment instruction result: %s', $text));
    }
}

assert_attachment_instruction('この画像をアニメ風に変換してください。', true);
assert_attachment_instruction('PDFを要約してください。', true);
assert_attachment_instruction('添付ファイルを確認してください。', true);
assert_attachment_instruction('昨日の会議は予定どおりです。', false);
assert_attachment_instruction('よろしくお願いします。', false);

if (line_agent_is_recent_group_attachment_followup(['source_type' => 'user', 'source_key' => 'user:Uxxx'], '画像を変換してください。')) {
    throw new RuntimeException('one-to-one message must not use group attachment follow-up logic');
}

echo "group attachment follow-up checks passed\n";
