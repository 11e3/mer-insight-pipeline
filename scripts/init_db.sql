-- mer-insight-pipeline DB 초기화
CREATE EXTENSION IF NOT EXISTS vector;

-- ─── 메르 포스트 원본 ─────────────────────────────────────────────────────────
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

-- HNSW 인덱스 (2000개 수준에서 ivfflat보다 빠르고 recall 높음)
CREATE INDEX IF NOT EXISTS idx_insights_embedding
    ON mer_insights USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─── 매크로 일별 데이터 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_daily (
    date          DATE PRIMARY KEY,
    kospi         FLOAT,
    kosdaq        FLOAT,
    usd_krw       FLOAT,
    us_10y        FLOAT,   -- 미국 10년물 금리
    kr_base_rate  FLOAT,   -- 한국 기준금리
    wti           FLOAT,
    btc_usd       FLOAT,
    vix           FLOAT,
    fed_funds_rate  FLOAT,
    us_cpi_yoy      FLOAT,   -- 미국 CPI YoY
    us_unemployment FLOAT    -- 미국 실업률
);

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
    is_correct          BOOLEAN,
    skipped_at          DATE,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─── 실시간 이벤트 ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(20) NOT NULL
                CHECK (event_type IN (
                    'dart','news','mer_new_post','macro_alert',
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

-- ─── 자동 분석 결과 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auto_analyses (
    id                SERIAL PRIMARY KEY,
    event_id          INTEGER REFERENCES events(id) ON DELETE CASCADE,
    analysis_text     TEXT,
    rules_used        INTEGER[],   -- 사용된 mer_insights.id 목록
    macro_context     JSONB,
    telegram_sent     BOOLEAN DEFAULT FALSE,
    telegram_sent_at  TIMESTAMP,
    created_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_event_id ON auto_analyses (event_id);

-- ─── Observability: LLM 호출 추적 ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS traces (
    id          VARCHAR(36) PRIMARY KEY,  -- UUID
    name        TEXT NOT NULL,
    start_time  TIMESTAMP NOT NULL,
    end_time    TIMESTAMP,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cost_usd      FLOAT DEFAULT 0,
    status      VARCHAR(20) DEFAULT 'running'  -- running | ok | error
                CHECK (status IN ('running','ok','error')),
    metadata    JSONB
);

CREATE INDEX IF NOT EXISTS idx_traces_start ON traces (start_time);

CREATE TABLE IF NOT EXISTS spans (
    id              VARCHAR(36) PRIMARY KEY,  -- UUID
    trace_id        VARCHAR(36) REFERENCES traces(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    model           TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    latency_ms      INTEGER,
    cost_usd        FLOAT DEFAULT 0,
    tool_calls      TEXT[],
    error           TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_created  ON spans (created_at);
