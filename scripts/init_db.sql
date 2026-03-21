-- insight-verify DB 초기화
CREATE EXTENSION IF NOT EXISTS vector;

-- ─── 소스 관리 ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id          SERIAL PRIMARY KEY,
    source_type VARCHAR(20) NOT NULL,    -- 'blog', 'twitter', 'report'
    name        VARCHAR(100) NOT NULL UNIQUE,  -- 'mer_ranto28', 'checkmatey'
    platform    VARCHAR(50),             -- 'naver_blog', 'x_twitter', 'web'
    url         TEXT,
    config      JSONB DEFAULT '{}',
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- 기본 소스: 메르 블로그
INSERT INTO sources (source_type, name, platform, url)
VALUES ('blog', 'mer_ranto28', 'naver_blog', 'https://blog.naver.com/ranto28')
ON CONFLICT (name) DO NOTHING;

-- Arthur Hayes (Crypto Trader Digest)
INSERT INTO sources (source_type, name, platform, url, config)
VALUES ('substack', 'arthur_hayes', 'substack', 'https://cryptohayes.substack.com',
        '{"feed_url": "https://cryptohayes.substack.com/feed"}'::jsonb)
ON CONFLICT (name) DO NOTHING;

-- ─── 포스트 원본 ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mer_posts (
    id          SERIAL PRIMARY KEY,
    log_no      VARCHAR(20) UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    date        DATE,
    url         TEXT,
    content_text TEXT,
    image_urls  TEXT[],
    tags        TEXT[],
    word_count  INTEGER,
    source_id   INTEGER REFERENCES sources(id),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mer_posts_date ON mer_posts (date);
CREATE INDEX IF NOT EXISTS idx_mer_posts_log_no ON mer_posts (log_no);

-- ─── 추출된 인사이트 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mer_insights (
    id              SERIAL PRIMARY KEY,
    post_id         INTEGER REFERENCES mer_posts(id) ON DELETE CASCADE,
    insight_type    VARCHAR(20) NOT NULL
                    CHECK (insight_type IN ('rule','prediction','evaluation','macro_view')),
    content         TEXT NOT NULL,
    structured_data JSONB,
    confidence      FLOAT CHECK (confidence BETWEEN 0 AND 1),
    embedding       vector(1024),
    cluster_id      INTEGER,
    is_canonical    BOOLEAN,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_insights_post_id ON mer_insights (post_id);
CREATE INDEX IF NOT EXISTS idx_insights_type    ON mer_insights (insight_type);
CREATE UNIQUE INDEX IF NOT EXISTS uq_insights_content
    ON mer_insights (post_id, insight_type, md5(content));

-- HNSW 인덱스 (2000개 수준에서 ivfflat보다 빠르고 recall 높음)
CREATE INDEX IF NOT EXISTS idx_insights_embedding
    ON mer_insights USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─── 예측 트래킹 ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mer_predictions (
    id                  SERIAL PRIMARY KEY,
    insight_id          INTEGER REFERENCES mer_insights(id) ON DELETE CASCADE,
    prediction_text     TEXT,
    predicted_direction VARCHAR(10) CHECK (predicted_direction IN ('up','down','neutral')),
    target_asset        VARCHAR(100),
    prediction_date     DATE,
    verification_date   DATE,
    actual_outcome      TEXT,
    source_url          TEXT,             -- 검증 근거 링크
    source_id           INTEGER REFERENCES sources(id),
    is_correct          BOOLEAN,
    skipped_at          DATE,
    expected_date       DATE,         -- 미래 예측의 예상 실현 시점 (해당 날짜까지 검증 건너뜀)
    is_verifiable       BOOLEAN,      -- Haiku 분류: 검증 가능/불가 (NULL=미분류)
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_predictions_text
    ON mer_predictions (md5(prediction_text));
CREATE INDEX IF NOT EXISTS idx_predictions_pending
    ON mer_predictions (is_correct, is_verifiable)
    WHERE is_correct IS NULL;

-- ─── 뉴스 헤드라인 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_headlines (
    id          SERIAL PRIMARY KEY,
    headline    TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    source_name VARCHAR(200),
    language    VARCHAR(10) DEFAULT 'ko',
    published_at TIMESTAMP NOT NULL,
    keywords    TEXT[],
    feed_topic  VARCHAR(50),
    embedding   vector(1024),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_headlines_url
    ON news_headlines (md5(source_url));
CREATE INDEX IF NOT EXISTS idx_headlines_published
    ON news_headlines (published_at);
CREATE INDEX IF NOT EXISTS idx_headlines_keywords
    ON news_headlines USING gin (keywords);
CREATE INDEX IF NOT EXISTS idx_headlines_embedding
    ON news_headlines USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─── 뉴스 피드 커서 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_feed_cursors (
    feed_id         VARCHAR(100) PRIMARY KEY,
    last_fetched_at TIMESTAMP,
    item_count      INTEGER DEFAULT 0
);

-- ─── 실시간 이벤트 ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(20) NOT NULL
                CHECK (event_type IN (
                    'mer_new_post',
                    'report_daily','report_weekly','report_monthly',
                    'report_quarterly','report_annual'
                )),
    source      TEXT,
    title       TEXT,
    content     TEXT,
    event_date  TIMESTAMP,
    embedding   vector(1024),  -- 유사 이벤트 검색용
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_date ON events (event_date);
CREATE INDEX IF NOT EXISTS idx_events_embedding
    ON events USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
