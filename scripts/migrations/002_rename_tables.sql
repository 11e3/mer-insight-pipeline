-- 002: mer_posts → posts, mer_insights → insights, mer_predictions → predictions
-- 기존 인덱스/제약조건은 테이블 rename 시 자동으로 따라감

ALTER TABLE IF EXISTS mer_posts RENAME TO posts;
ALTER TABLE IF EXISTS mer_insights RENAME TO insights;
ALTER TABLE IF EXISTS mer_predictions RENAME TO predictions;

-- 백업 테이블도 있으면 rename (선택)
ALTER TABLE IF EXISTS mer_predictions_verdict_backup_20260319
    RENAME TO predictions_verdict_backup_20260319;
