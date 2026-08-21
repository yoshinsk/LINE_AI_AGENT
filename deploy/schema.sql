-- <PROJECT_ROOT>\deploy\schema.sql
-- LINE Webhook、会話履歴、検索用ナレッジ、ワーカージョブを保持するMariaDB/MySQL互換DBスキーマです。

CREATE TABLE IF NOT EXISTS line_sources (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_key VARCHAR(191) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_external_id VARCHAR(191) NOT NULL,
  display_name VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_line_sources_source_key (source_key),
  KEY idx_line_sources_external (source_type, source_external_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_webhook_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  webhook_event_id VARCHAR(191) NULL,
  event_hash CHAR(64) NOT NULL,
  source_key VARCHAR(191) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  raw_json LONGTEXT NOT NULL,
  received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_line_webhook_events_event_hash (event_hash),
  UNIQUE KEY uq_line_webhook_events_webhook_event_id (webhook_event_id),
  KEY idx_line_webhook_events_source_received (source_key, received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_messages (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_key VARCHAR(191) NOT NULL,
  role VARCHAR(32) NOT NULL,
  line_message_id VARCHAR(191) NULL,
  body MEDIUMTEXT NOT NULL,
  raw_json LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line_messages_source_created (source_key, created_at),
  KEY idx_line_messages_line_message_id (line_message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_jobs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_id BIGINT UNSIGNED NULL,
  source_key VARCHAR(191) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_external_id VARCHAR(191) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  request_text MEDIUMTEXT NOT NULL,
  project_ref VARCHAR(1024) NULL,
  worker_id VARCHAR(191) NULL,
  lease_until DATETIME NULL,
  result_text MEDIUMTEXT NULL,
  error_text MEDIUMTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line_jobs_claim (status, created_at),
  KEY idx_line_jobs_source_status (source_key, status, created_at),
  KEY idx_line_jobs_lease (status, lease_until),
  CONSTRAINT fk_line_jobs_event_id FOREIGN KEY (event_id) REFERENCES line_webhook_events(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_attachments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_id BIGINT UNSIGNED NULL,
  job_id BIGINT UNSIGNED NULL,
  source_key VARCHAR(191) NOT NULL,
  line_message_id VARCHAR(191) NOT NULL,
  message_type VARCHAR(32) NOT NULL,
  original_file_name VARCHAR(255) NOT NULL,
  file_size BIGINT UNSIGNED NULL,
  content_type VARCHAR(191) NULL,
  sha256 CHAR(64) NULL,
  storage_path VARCHAR(2048) NULL,
  storage_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  metadata_json LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_line_attachments_line_message_id (line_message_id),
  KEY idx_line_attachments_source_created (source_key, created_at),
  KEY idx_line_attachments_job (job_id),
  CONSTRAINT fk_line_attachments_event_id FOREIGN KEY (event_id) REFERENCES line_webhook_events(id) ON DELETE SET NULL,
  CONSTRAINT fk_line_attachments_job_id FOREIGN KEY (job_id) REFERENCES line_jobs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 同じ添付を「受信時の要約」と「後続の編集依頼」の両方へ安全に関連付けます。
CREATE TABLE IF NOT EXISTS line_job_attachment_links (
  job_id BIGINT UNSIGNED NOT NULL,
  attachment_id BIGINT UNSIGNED NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id, attachment_id),
  KEY idx_line_job_attachment_links_attachment (attachment_id, created_at),
  CONSTRAINT fk_line_job_attachment_links_job_id FOREIGN KEY (job_id) REFERENCES line_jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_line_job_attachment_links_attachment_id FOREIGN KEY (attachment_id) REFERENCES line_attachments(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 旧実装で1件だけ保持していた関連を、新しい多対多テーブルへ移行します。
INSERT IGNORE INTO line_job_attachment_links (job_id, attachment_id)
SELECT job_id, id
  FROM line_attachments
 WHERE job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS line_knowledge_chunks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_key VARCHAR(191) NOT NULL,
  message_id BIGINT UNSIGNED NULL,
  job_id BIGINT UNSIGNED NULL,
  role VARCHAR(32) NOT NULL,
  text TEXT NOT NULL,
  metadata_json LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line_knowledge_source_created (source_key, created_at),
  FULLTEXT KEY ft_line_knowledge_text (text),
  CONSTRAINT fk_line_knowledge_message_id FOREIGN KEY (message_id) REFERENCES line_messages(id) ON DELETE SET NULL,
  CONSTRAINT fk_line_knowledge_job_id FOREIGN KEY (job_id) REFERENCES line_jobs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_delivery_attempts (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  job_id BIGINT UNSIGNED NULL,
  source_key VARCHAR(191) NOT NULL,
  delivery_type VARCHAR(32) NOT NULL,
  status_code INT NULL,
  response_body MEDIUMTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line_delivery_job (job_id, created_at),
  CONSTRAINT fk_line_delivery_job_id FOREIGN KEY (job_id) REFERENCES line_jobs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS line_worker_heartbeats (
  worker_id VARCHAR(191) NOT NULL,
  status_text VARCHAR(255) NOT NULL,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  metadata_json LONGTEXT NULL,
  PRIMARY KEY (worker_id),
  KEY idx_line_worker_heartbeats_last_seen (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
