# CLAUDE.md — mer-insight-pipeline

## 프로젝트 개요

메르(ranto28) 네이버 블로그 모니터링 → Claude로 인사이트 추출 → 예측 수동 검증 파이프라인.
PostgreSQL + pgvector 기반 하이브리드 검색 (BM25 + 벡터), 예측 수동 검증 (자동화 불가 — README 실험 결과 참조).

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
```

## 핵심 Gotcha

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
cd /path/to/mer-insight-pipeline
python scripts/eval/expand_eval_dataset.py  # ✓
```

### 통합 테스트 환경변수
단위 테스트는 `DATABASE_URL` 불필요. 통합 테스트는 `TEST_DATABASE_URL` 별도 설정 필요 (미설정 시 자동 스킵).

### GCP Cloud Run Job vs 로컬 APScheduler
- **로컬**: `event_dispatcher.py`가 APScheduler로 매일 01:00 단일 잡 실행
- **GCP**: `run_job.py`로 전체 파이프라인 1회 실행 (메르 글 수집 + 검증)

### 예측 검증은 수동만 가능
- API 자동 검증 실험 결과: 판정 상반(CORRECT↔INCORRECT) 발생 → DB 오염 위험
- `verifier.py`는 검증 대기 예측을 **내보내기 + 텔레그램 알림**만 수행
- 실제 판정은 claude.ai에서 수동으로 진행 후 `import_manual_verdicts.py`로 반영
- 상세 실험 결과: README.md "Why Not Fully Automated?" 참조
- **배치 크기 20건 제한** — 50건 이상 배치 시 ID-verdict 밀림 발생
- **source_url 필수** — URL 없는 CORRECT/INCORRECT는 import 시 스킵
- **grouped/ 폴더 import 금지** — 오염 원인 확인됨

### 뉴스 헤드라인 DB
- `news_headlines` 테이블: 헤드라인 + source_url + keywords(TEXT[] GIN) + published_at
- 수집: `src/collect/news_collector.py` — Google News RSS 15개 피드, 일일 자동 수집
- 키워드: `src/collect/keyword_extractor.py` — kiwipiepy(한국어) + regex(영어), LLM 없음
- 매칭: `src/verify/headline_matcher.py` — prediction ↔ headline 키워드 GIN 매칭
- 백필: `scripts/ops/backfill_news.py --start 2022-01 --end 2025-12`
- event_dispatcher에서 매일 자동 수집 (파이프라인 step 2)

### 자동 검증 (AutoVerifier)
- `src/verify/auto_verifier.py`: headline match → Haiku 1건씩 판정 → DB 반영
- source_url 없는 CORRECT/INCORRECT는 DB 반영 안 함
- daily_limit=200, CALL_DELAY=0.5
- event_dispatcher step 3a에서 자동 실행, 3b에서 나머지 수동 내보내기

### 예측 추출 형식 (신규)
- `claim`: yes/no로 답할 수 있는 검증 가능한 명제
- `search_keywords`: 뉴스 검색용 키워드 3-5개
- `expected_date`: 검증 가능 시점 YYYY-MM-DD
- 기존 5,020건은 구형식 (claim/keywords 없음), 신규 글부터 적용

### 검증 데이터 리셋 이력
- 2026-03-19: 배치 검증 오염(36%, 50건 블라인드 감사) 확인 → 전체 verdict 리셋
- 백업: `mer_predictions_verdict_backup_20260319` 테이블
- 리셋 후 정책: 배치 크기 20건, source_url 필수, grouped/ import 금지

### DB 연결 패턴
- `src/db/connection.py`의 `connect()`, `get_pool()` async context manager 사용
- 수동 `conn.close()` 대신 `async with connect() as conn:` 패턴

### Source Abstraction Layer
- `sources` 테이블: source_type, name, platform, config
- `mer_posts`, `mer_predictions`에 `source_id` FK 추가
- `SourceCollector` 프로토콜 (`src/collect/source_protocol.py`)
- `get_collector()` 팩토리 (`src/collect/factory.py`)
- `MerMonitor`가 `SourceCollector` 구현체
- `event_dispatcher`가 `sources` 테이블에서 active collectors 로드

### 삭제된 모듈
과거 존재했으나 제거된 모듈: `src/agent/`, `src/guard/`, `src/observability/`, `src/ingest/`, `src/collect/dart.py`, `src/collect/macro.py`, `src/verify/context.py`, `src/verify/search.py`. import 참조 발견 시 삭제된 것으로 간주.

## 아키텍처 요약

```
src/
├── config/         # 설정 (settings.py, prompts.py)
├── db/             # 공유 DB 유틸리티 (connect, get_pool)
├── embed/          # 임베딩
│   ├── protocol.py      # Embedder Protocol
│   ├── local.py         # multilingual-e5-large 1024-dim (기본)
│   ├── vertex.py        # Vertex AI 768-dim (GCP 전용)
│   ├── factory.py       # get_embedder() + vec_str()
│   └── backfill.py      # NULL 임베딩 일괄 생성
├── extract/        # Claude Batch API 인사이트 추출
│   ├── batch_api.py     # 배치 생성/상태/다운로드 CLI
│   ├── parse_results.py # JSONL → DB (INSIGHT_TYPE_MAP, extract_content)
│   └── realtime.py      # 실시간 인사이트 추출
├── collect/        # 데이터 수집
│   ├── source_protocol.py   # SourceCollector 프로토콜
│   ├── factory.py            # get_collector() 팩토리
│   ├── mer_monitor.py       # 블로그 RSS 감시 (SourceCollector 구현)
│   ├── posts.py             # JSON → mer_posts 적재
│   ├── date_parser.py       # 메르 블로그 날짜 파싱
│   ├── news_collector.py    # 뉴스 헤드라인 RSS 수집 (15개 피드)
│   ├── feeds.py             # RSS 피드 정의
│   └── keyword_extractor.py # 키워드 추출 (kiwipiepy + regex)
├── verify/         # 예측 검증
│   ├── verifier.py          # PredictionVerifier (내보내기 + 텔레그램)
│   ├── auto_verifier.py     # AutoVerifier (Haiku 1건씩 자동 판정)
│   ├── headline_matcher.py  # 예측 ↔ 헤드라인 키워드 매칭
│   └── prompt.py            # 상수 (배치 크기)
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
│   └── event_dispatcher.py  # APScheduler / Cloud Run Job 진입점
└── dashboard/      # Streamlit 대시보드
    ├── app.py           # 레이아웃 + 렌더링
    ├── queries.py       # DB 쿼리 함수
    └── topics.py        # 주제 분류 (TOPIC_KEYWORDS)

scripts/
├── run_job.py           # GCP Cloud Run Job 진입점
├── run_batch.py         # 배치 추출 오케스트레이터
├── reembed_all.py       # 전체 재임베딩 (모델 교체 시)
├── naver_blog_scraper.py # 블로그 스크래퍼
├── ops/                 # 데이터 운영 스크립트
│   ├── export_*.py      # 예측 내보내기 (round1~5)
│   ├── import_*.py      # 수동 검증 결과 가져오기
│   ├── fill_*.py        # expected_date 등 필드 채우기
│   ├── migrate_predictions.py  # mer_insights → mer_predictions 소급 적재
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
| `daily_pipeline` | 메르 글 수집 + 검증 대기 내보내기 + 알림 | 매일 01:00 |

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `GCP_PROJECT_ID` | 선택 | Vertex AI 임베딩 활성화 |
| `GCP_LOCATION` | 선택 | Vertex AI 리전 (기본: us-central1) |

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
