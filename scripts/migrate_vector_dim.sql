-- vector(1024) → vector(768) 마이그레이션
-- Vertex AI text-multilingual-embedding-002 (768dim) 전환용
--
-- 주의: 기존 임베딩을 NULL로 초기화하고 재임베딩이 필요함.
-- 실행 후 python -m scripts.reembed_all 을 반드시 실행할 것.
--
-- Usage:
--   docker exec -i mer_db psql -U mer -d mer_pipeline < scripts/migrate_vector_dim.sql

BEGIN;

-- 1. HNSW 인덱스 제거 (타입 변경 전 필수)
DROP INDEX IF EXISTS idx_insights_embedding;
DROP INDEX IF EXISTS idx_events_embedding;

-- 2. 컬럼 타입 변경 (기존 임베딩 삭제 후 재생성 예정이므로 NULL로 초기화)
ALTER TABLE mer_insights ALTER COLUMN embedding TYPE vector(768) USING NULL;
ALTER TABLE events       ALTER COLUMN embedding TYPE vector(768) USING NULL;

-- 3. HNSW 인덱스 재생성
CREATE INDEX idx_insights_embedding
    ON mer_insights USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_events_embedding
    ON events USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 4. init_db.sql 반영용 확인
SELECT
    column_name,
    udt_name,
    character_maximum_length
FROM information_schema.columns
WHERE table_name IN ('mer_insights', 'events')
  AND column_name = 'embedding';

COMMIT;
