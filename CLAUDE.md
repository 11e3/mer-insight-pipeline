# CLAUDE.md — mer-insight-pipeline

## 프로젝트 개요

메르(ranto28) 네이버 블로그 모니터링 → Claude로 인사이트 추출 → 텔레그램 전송 파이프라인.
PostgreSQL + pgvector 기반 하이브리드 검색 (BM25 + 벡터), 예측 자동 검증.

## 주요 명령어

```bash
# 테스트 (단위)
pytest tests/ -v

# 테스트 (통합 — PostgreSQL 필요, TEST_DATABASE_URL 환경변수)
TEST_DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test pytest tests/test_integration_dispatcher.py -v

# 검색 ablation 실험
python -m src.search.experiment --mode ablation --dataset eval_data/gold_extended.json --k 5

# eval dataset 확장 (LLM 생성)
python scripts/expand_eval_dataset.py --dry-run 10
python scripts/expand_eval_dataset.py --target 200

# BM25 인덱스 캐시 빌드
python -m src.search.bm25_index  # 캐시 → data/bm25_cache.pkl

# 단일 잡 실행 (GCP Cloud Run Job 진입점)
python -m scripts.run_job --job mer_check
python -m scripts.run_job --job dart_check
python -m scripts.run_job --job macro_check
python -m scripts.run_job --job verify_predictions
```

## 핵심 Gotcha

### 임베딩 차원 (1024-dim 통일)
- DB, `init_db.sql`, `settings.py` 모두 **1024-dim** (`intfloat/multilingual-e5-large`)
- 기본 임베더: `LocalEmbedder` (`src/extract/local_embedder.py`) — GCP 불필요
- `VertexEmbedder` (768-dim)는 `GCP_PROJECT_ID` 설정 시에만 사용 (DB 재인덱싱 필요)
- `get_embedder()` 팩토리 함수가 환경에 따라 자동 선택

### src/search/__init__.py lazy import
`__getattr__` 기반 lazy import — `HybridSearcher` / `hybrid_search`만 노출. GCP 환경변수 없이도 `src.search.experiment` 등 실행 가능.

### scripts 실행 경로
`scripts/` 하위 모듈은 **프로젝트 루트에서** 실행해야 함:
```bash
cd /path/to/mer-insight-pipeline
python scripts/expand_eval_dataset.py  # ✓
```

### 통합 테스트 환경변수
단위 테스트는 `DATABASE_URL` 불필요. 통합 테스트는 `TEST_DATABASE_URL` 별도 설정 필요 (미설정 시 자동 스킵).

### GCP Cloud Run Job vs 로컬 APScheduler
- **로컬**: `event_dispatcher.py`가 APScheduler로 6개 스케줄 직접 실행
- **GCP**: `run_job.py --job <name>`으로 4개 잡 개별 호출 (`macro_check`가 macro_update + macro_alert + news 통합)

### Telegram — Tier 1 단일 채널
`TELEGRAM_TIER1_CHAT_ID` 하나만 존재. Tier 2 구현 없음. 전체 알림(예측 추출 / verdict 변경 / 에러)이 동일 채널로 발송.

## 아키텍처 요약

```
src/
├── search/         # 하이브리드 검색
│   ├── bm25_index.py     # kiwipiepy 형태소 분석 + rank-bm25 + pickle 캐시
│   ├── vector_index.py   # pgvector HNSW 래퍼 (1024-dim)
│   ├── hybrid.py         # RRF 융합 (α=0.6)
│   └── experiment.py     # ablation 실험 (multilingual-e5-large)
├── extract/        # Claude Batch API 인사이트 추출 + 임베딩
├── pipeline/       # 이벤트 디스패처, 모니터, 컨텍스트 어셈블러
├── eval/           # LLM 심판 평가 파이프라인
├── ingest/         # FRED / 한국은행 ECOS 매크로 수집
├── delivery/       # 텔레그램 전송 (Tier 1 단일 채널)
└── dashboard/      # Streamlit 대시보드 (비용·지연 / 예측 적중률)

scripts/
├── run_job.py            # GCP Cloud Run Job 진입점
├── run_batch.py          # 배치 추출 오케스트레이터
├── expand_eval_dataset.py # gold_extended.json 자동 확장
├── cluster_insights.py   # DBSCAN 중복 제거
├── reembed_all.py        # 전체 재임베딩 (모델 교체 시)
└── migrate_predictions.py # mer_insights → mer_predictions 소급 적재
```

## GCP Cloud Run 잡

| 잡 이름 | 동작 | 스케줄 |
|---------|------|--------|
| `mer_check` | 메르 신규 글 확인 + 인사이트 추출 | 매 5분 |
| `dart_check` | DART 공시 수집 | 매 10분, 8–18시 평일 |
| `macro_check` | 매크로 갱신 + 급변 알림 + 뉴스 수집 | 매 30분 |
| `verify_predictions` | 미검증 예측 배치 판정 (Haiku) | 매일 20:00 |

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `TELEGRAM_BOT_TOKEN` | 전송 시 | 텔레그램 봇 토큰 |
| `TELEGRAM_TIER1_CHAT_ID` | 전송 시 | 텔레그램 채널 ID |
| `GCP_PROJECT_ID` | 선택 | Vertex AI 임베딩 활성화 |
| `GCP_LOCATION` | 선택 | Vertex AI 리전 (기본: us-central1) |
| `FRED_API_KEY` | 선택 | FRED 거시경제 데이터 |
| `BOK_API_KEY` | 선택 | 한국은행 ECOS |

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
