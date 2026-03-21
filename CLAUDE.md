# CLAUDE.md — insight-verify

## 프로젝트 개요

다중 소스 금융 블로그/뉴스레터 모니터링 → Claude로 인사이트 추출 → 뉴스 헤드라인 DB 기반 자동 검증 파이프라인.
PostgreSQL + pgvector 기반 하이브리드 검색 (BM25 + 벡터). 검증은 3계층: 헤드라인 매칭 자동 → 데이터 API(예정) → 수동.

## 주요 명령어

```bash
# 테스트 (단위)
pytest tests/ -v

# 테스트 (통합 — PostgreSQL 필요, TEST_DATABASE_URL 환경변수)
TEST_DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test pytest tests/test_integration_dispatcher.py -v

# 검색 ablation 실험
python -m src.eval.experiment --mode ablation --dataset eval_data/gold_extended.json --k 5

# eval dataset 확장 (LLM 생성)
python scripts/eval/expand_eval_dataset.py --dry-run 10
python scripts/eval/expand_eval_dataset.py --target 200

# BM25 인덱스 캐시 빌드
python -m src.search.bm25_index  # 캐시 → data/bm25_cache.pkl

# 단일 잡 실행 (GCP Cloud Run Job 진입점)
python -m scripts.run_job

# DB 기동
docker compose up -d db

# 대시보드
streamlit run src/dashboard/app.py

# 배치 검증 (Batch API)
python -m scripts.ops.batch_verify create   # 매칭 + 배치 제출
python -m scripts.ops.batch_verify status   # 진행 확인 + 결과 다운로드
python -m scripts.ops.batch_verify apply    # 결과 DB 반영

# Substack 아카이브 백필
python -m scripts.ops.backfill_substack --source arthur_hayes --dry-run
python -m scripts.ops.backfill_substack --source arthur_hayes
```

## 핵심 Gotcha

### DB 테이블명
- 테이블: `posts`, `insights`, `predictions` (mer_ 접두사 제거됨, migration 002)
- 과거 migration (001, migrate_vector_dim)에는 구 테이블명 `mer_posts` 등이 그대로 남아있음 — 이미 실행된 기록이므로 수정하지 않음

### 임베딩 차원 (1024-dim 통일)
- DB, `init_db.sql`, `src/config/settings.py` 모두 **1024-dim** (`intfloat/multilingual-e5-large`)
- `src/embed/protocol.py`: `Embedder` 프로토콜
- `src/embed/factory.py`: `get_embedder()` 팩토리 + `vec_str()`
- 기본: `LocalEmbedder` (`src/embed/local.py`) — GCP 불필요
- `VertexEmbedder` (`src/embed/vertex.py`, 768-dim)는 `GCP_PROJECT_ID` 설정 시에만 활성화 (DB 비호환, 재인덱싱 필요)

### src/search/__init__.py lazy import
`__getattr__` 기반 lazy import — `hybrid.py → embed/` 체인이 GCP 없이도 크래시하지 않도록.

### scripts 실행 경로
`scripts/` 하위 모듈은 **프로젝트 루트에서** 실행해야 함:
```bash
cd /path/to/insight-verify
python -m scripts.ops.backfill_substack --source arthur_hayes  # ✓
```

### 통합 테스트 환경변수
단위 테스트는 `DATABASE_URL` 불필요. 통합 테스트는 `TEST_DATABASE_URL` 별도 설정 필요 (미설정 시 자동 스킵).

### GCP Cloud Run Job vs 로컬 APScheduler
- **로컬**: `event_dispatcher.py`가 APScheduler로 매일 01:00 단일 잡 실행
- **GCP**: `run_job.py`로 전체 파이프라인 1회 실행 (소스별 수집 + 검증)

### 예측 검증 (3경로, 자동만)
1. **헤드라인 매칭 자동**: `auto_verifier.py` — headline_matcher(키워드 GIN + 벡터 fallback)로 매칭 → Haiku verdict + reason(헤드라인N 인용) → source_url은 최고 overlap 헤드라인에서 자동 할당
2. **배치 자동**: `scripts/ops/batch_verify.py create/status/apply` — Batch API 50% 할인, apply 시 헤드라인N을 마크다운 링크로 치환
3. **Haiku 직접 판정**: `scripts/ops/reclassify_pending.py` — knowledge cutoff 이전 예측은 헤드라인 없이 Haiku가 자체 지식으로 판정 (source_url 없음, reason만)
- **source_url 필수** — 헤드라인 매칭 검증에서 URL 없는 CORRECT/INCORRECT는 DB 반영 안 함 (Haiku 직접 판정은 예외)
- **UPDATE WHERE is_correct IS NULL** — verdict 덮어쓰기 방지
- **skipped_at 7일 쿨다운** — PENDING 판정 후 7일간 재시도 방지
- **is_verifiable 분류** — Haiku Batch API로 검증 가능/불가 분류
- 수동 검증 스텝은 event_dispatcher에서 제거됨

### 뉴스 헤드라인 DB
- `news_headlines` 테이블: 헤드라인 + source_url + keywords(TEXT[] GIN) + embedding(vector 1024) + published_at
- 수집: `src/collect/news_collector.py` — Google News RSS 33개 피드(한국어+영어) + Naver News API 14개 쿼리, 일일 자동 수집
- 키워드: `src/collect/keyword_extractor.py` — kiwipiepy(한국어) + 복합 명사구 + regex(영어), LLM 없음
- 영어 복합 명사구: `_EN_COMPOUND_TERMS` — "federal reserve", "interest rate", "bitcoin etf" 등 금융 도메인 용어를 단일 키워드로 추출
- 매칭: `src/verify/headline_matcher.py` — 1차 키워드 GIN 매칭 → 2차 벡터 유사도 fallback (cosine ≥ 0.45)
- 백필: `scripts/ops/backfill_news.py --start 2022-01 --end 2025-12`
- event_dispatcher에서 매일 자동 수집 (파이프라인 step 2)

### 자동 검증 (AutoVerifier)
- `src/verify/auto_verifier.py`: headline match → Haiku verdict + reason만 반환 → source_url은 최고 overlap 헤드라인에서 자동 할당
- `scripts/ops/batch_verify.py`: Batch API로 일괄 검증 (50% 할인)
- 키워드 불용어 필터 적용 (`_KO_STOPWORDS` in keyword_extractor.py)
- 날짜 범위: `prediction_date + 1일 ~ 현재` (예측 당일 뉴스 제외)
- `MIN_KEYWORD_OVERLAP = 3` (2는 false positive 많음)
- daily_limit=200, event_dispatcher step 3a에서 자동 실행

### 예측 추출 형식
- `claim`: yes/no로 답할 수 있는 검증 가능한 명제
- `search_keywords`: 뉴스 검색용 키워드 3-5개
- `expected_date`: 검증 가능 시점 YYYY-MM-DD
- 영문 소스(Substack, web)는 `ENGLISH_INSIGHT_SYSTEM_PROMPT` + `ENGLISH_USER_TEMPLATE` 사용
- `parse_results.py`의 `extract_content()`는 한글/영문 키 모두 처리 (mer_assessment or assessment)

### 검증 데이터 리셋 이력
- 2026-03-19: 배치 검증 오염(36%, 50건 블라인드 감사) 확인 → 전체 verdict 리셋
- 백업: `predictions_verdict_backup_20260319` 테이블
- 리셋 후 정책: 배치 크기 20건, source_url 필수, grouped/ import 금지

### DB 연결 패턴
- `src/db/connection.py`의 `connect()`, `get_pool()` async context manager 사용
- 수동 `conn.close()` 대신 `async with connect() as conn:` 패턴
- 대시보드: `psycopg2.pool.SimpleConnectionPool` + `st.cache_resource` (queries.py)

### Source Abstraction Layer
- `sources` 테이블: source_type, name, platform, config
- `posts`, `predictions`에 `source_id` FK 추가
- `SourceCollector` 프로토콜 (`src/collect/source_protocol.py`)
- `get_collector()` 팩토리 (`src/collect/factory.py`) — config에 `feed_url`이 있으면 `RSSCollector` 자동 사용
- `MerMonitor`가 `SourceCollector` 구현체 (네이버 블로그)
- `RSSCollector`가 Substack/일반 RSS 처리 (feed_url config)
- `event_dispatcher`가 `sources` 테이블에서 active collectors 로드
- Substack 백필: `scripts/ops/backfill_substack.py` — archive API 페이지네이션 + 개별 포스트 스크래핑

### 활성 소스
| name | source_type | platform | 상태 |
|------|-------------|----------|------|
| mer_ranto28 | blog | naver_blog | active (5,010 predictions) |
| arthur_hayes | substack | substack | active (150 predictions, 72 posts backfilled) |
| glassnode_insights | blog | web | **inactive** (주간 차트 리포트 위주, extraction yield 낮음) |

### 삭제된 모듈
과거 존재했으나 제거된 모듈: `src/agent/`, `src/guard/`, `src/observability/`, `src/ingest/`, `src/collect/dart.py`, `src/collect/macro.py`, `src/verify/context.py`, `src/verify/search.py`. import 참조 발견 시 삭제된 것으로 간주.

## 아키텍처 요약

```
src/
├── config/         # 설정 (settings.py, prompts.py — 한국어/영어 프롬프트)
├── db/             # 공유 DB 유틸리티 (connect, get_pool)
├── embed/          # 임베딩
│   ├── protocol.py      # Embedder Protocol
│   ├── local.py         # multilingual-e5-large 1024-dim (기본)
│   ├── vertex.py        # Vertex AI 768-dim (GCP 전용)
│   ├── factory.py       # get_embedder() + vec_str()
│   └── backfill.py      # NULL 임베딩 일괄 생성
├── extract/        # Claude 인사이트 추출
│   ├── batch_api.py     # 배치 생성/상태/다운로드 CLI
│   ├── parse_results.py # JSONL → DB (한글/영문 키 호환)
│   └── realtime.py      # 실시간 추출 (source_type별 프롬프트 분기)
├── collect/        # 데이터 수집
│   ├── source_protocol.py   # SourceCollector 프로토콜
│   ├── factory.py            # get_collector() 팩토리
│   ├── mer_monitor.py       # 네이버 블로그 RSS (SourceCollector 구현)
│   ├── rss_collector.py     # 범용 RSS 수집기 (Substack 등)
│   ├── posts.py             # JSON → posts 적재
│   ├── date_parser.py       # 메르 블로그 날짜 파싱
│   ├── news_collector.py    # 뉴스 헤드라인 수집 (RSS 33피드 + Naver API 14쿼리)
│   ├── feeds.py             # RSS 피드 정의 (한국어 18 + 영어 15 + 크립토 5)
│   └── keyword_extractor.py # 키워드 추출 (kiwipiepy + 복합 명사구 + regex)
├── verify/         # 예측 검증
│   ├── verifier.py          # PredictionVerifier (내보내기 + 텔레그램)
│   ├── auto_verifier.py     # AutoVerifier (Haiku 1건씩 자동 판정)
│   ├── headline_matcher.py  # 예측 ↔ 헤드라인 하이브리드 매칭 (키워드 GIN + 벡터 fallback)
│   └── prompt.py            # 상수 + parse_llm_json() 공통 유틸
├── search/         # 하이브리드 검색
│   ├── bm25_index.py    # kiwipiepy 형태소 분석 + rank-bm25 + pickle 캐시
│   ├── vector_index.py  # pgvector HNSW 래퍼 (1024-dim)
│   └── hybrid.py        # RRF 융합 (α=0.6)
├── eval/           # 평가 파이프라인
│   ├── experiment.py    # ablation 실험 (Retrieval A/B 테스트)
│   ├── eval_runner.py   # 평가 오케스트레이터
│   ├── llm_judge.py     # Claude Sonnet LLM-as-judge
│   ├── metrics.py       # precision_at_k/recall_at_k/MRR
│   └── report.py        # 평가 결과 리포트
├── pipeline/       # 오케스트레이터
│   └── event_dispatcher.py  # APScheduler / Cloud Run Job 진입점 (step별 분리)
└── dashboard/      # Streamlit 대시보드
    ├── app.py           # 레이아웃 + 렌더링
    ├── queries.py       # DB 쿼리 (SimpleConnectionPool)
    └── topics.py        # 주제 분류 (TOPIC_KEYWORDS)

scripts/
├── run_job.py           # GCP Cloud Run Job 진입점
├── run_batch.py         # 배치 추출 오케스트레이터
├── reembed_all.py       # 전체 재임베딩 (모델 교체 시)
├── naver_blog_scraper.py # 블로그 스크래퍼
├── ops/                 # 데이터 운영 스크립트
│   ├── batch_verify.py       # Batch API 일괄 검증 (create/status/apply)
│   ├── backfill_news.py     # 뉴스 헤드라인 역사 백필
│   ├── backfill_substack.py # Substack 아카이브 백필 (archive API + 스크래핑)
│   ├── export_*.py      # 예측 내보내기 (round1~5)
│   ├── import_*.py      # 수동 검증 결과 가져오기
│   ├── fill_*.py        # expected_date 등 필드 채우기
│   ├── migrate_predictions.py  # insights → predictions 소급 적재
│   ├── populate_topics.py      # 주제 일괄 분류
│   ├── cluster_insights.py     # DBSCAN 중복 제거
│   └── regroup_by_topic.py     # 주제별 재분류
└── eval/                # 평가 관련 스크립트
    ├── expand_eval_dataset.py  # gold_extended.json 자동 확장
    └── compare_judges.py       # 심판 비교
```

## GCP Cloud Run 잡

| 잡 이름 | 동작 | 스케줄 |
|---------|------|--------|
| `daily_pipeline` | 소스별 글 수집 + 뉴스 수집 + 자동 검증 | 매일 01:00 (GitHub Actions cron) |

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `NAVER_CLIENT_ID` | 선택 | 네이버 뉴스 API (뉴스 수집 보강) |
| `NAVER_CLIENT_SECRET` | 선택 | 네이버 뉴스 API |
| `TELEGRAM_BOT_TOKEN` | 선택 | 텔레그램 알림 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 선택 | 텔레그램 채팅 ID |

## 검색 실험 결과 (search_experiment.json 기준)

- 데이터셋: `eval_data/gold_extended.json` (N=200 쿼리, K=5)
- 임베딩: `intfloat/multilingual-e5-large` (1024-dim)

| α | Precision@5 | Recall@5 | MRR |
|---|-------------|----------|-----|
| 0.0 (vector-only) | 0.199 | 0.995 | **0.995** |
| **0.6 (production)** ★ | **0.200** | **1.000** | 0.968 |
| 1.0 (BM25-only) | 0.196 | 0.980 | 0.935 |

- **프로덕션 α=0.6**: 유일하게 완전한 Recall(1.000) 달성 → 예측 누락 방지 목적으로 채택
- 최고 MRR은 vector-only(α=0.0, 0.995). 하이브리드는 MRR을 소폭 낮추는 대신 완전한 Recall 확보
- BM25-only(α=1.0)는 Recall·MRR 모두 vector-only보다 낮음
