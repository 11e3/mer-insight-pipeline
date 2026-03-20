-- 001_source_abstraction.sql
-- 다중 소스 지원을 위한 스키마 확장 (멱등)

-- 1. sources 테이블
CREATE TABLE IF NOT EXISTS sources (
    id          SERIAL PRIMARY KEY,
    source_type VARCHAR(20) NOT NULL,
    name        VARCHAR(100) NOT NULL UNIQUE,
    platform    VARCHAR(50),
    url         TEXT,
    config      JSONB DEFAULT '{}',
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 2. 기존 테이블에 source_id FK 추가
ALTER TABLE mer_posts ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES sources(id);
ALTER TABLE mer_predictions ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES sources(id);

-- 3. 기존 데이터 마이그레이션: 메르 블로그를 source_id=1로
INSERT INTO sources (source_type, name, platform, url)
VALUES ('blog', 'mer_ranto28', 'naver_blog', 'https://blog.naver.com/ranto28')
ON CONFLICT (name) DO NOTHING;

UPDATE mer_posts SET source_id = (SELECT id FROM sources WHERE name = 'mer_ranto28')
WHERE source_id IS NULL;

UPDATE mer_predictions SET source_id = (SELECT id FROM sources WHERE name = 'mer_ranto28')
WHERE source_id IS NULL;

-- 4. 인덱스
CREATE INDEX IF NOT EXISTS idx_posts_source_id ON mer_posts(source_id);
CREATE INDEX IF NOT EXISTS idx_predictions_source_id ON mer_predictions(source_id);
