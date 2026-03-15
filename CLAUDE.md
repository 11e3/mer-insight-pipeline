# CLAUDE.md — mer-insight-pipeline

## 프로젝트 개요
메르(ranto28) 네이버 블로그 모니터링 → Claude로 인사이트 추출 → 텔레그램 전송 파이프라인.
PostgreSQL + pgvector 기반 하이브리드 검색 (BM25 + 벡터), LLM 에이전트 루프, 예측 자동 검증.

## 주요 명령어

```bash
# 테스트 (단위)
pytest tests/ -v

# 테스트 (통합 — PostgreSQL 필요)
pytest tests/test_integration_dispatcher.py -v

# 검색 ablation 실험
python -m src.search.experiment --mode ablation --dataset eval_data/gold_extended.json --k 5

# eval dataset 확장 (LLM 생성)
python scripts/expand_eval_dataset.py --dry-run 10
python scripts/expand_eval_dataset.py --target 200

# BM25 인덱스 캐시 빌드
python -c "import asyncio, asyncpg, os; from src.search.bm25_index import BM25Index; from dotenv import load_dotenv; load_dotenv(); asyncio.run((lambda: __import__('asyncio').get_event_loop().run_until_complete(None))())"
# (더 간단한 방법)
python -m src.search.bm25_index  # 캐시 → data/bm25_cache.pkl

# 단일 잡 실행
python -m scripts.run_job --job mer_check
```

## 핵심 Gotcha

### DB 임베딩 차원 불일치
- **`init_db.sql`에는 `vector(768)`로 표기되어 있지만 실제 DB 컬럼은 `vector(1024)`**
- 2023년 `intfloat/multilingual-e5-large` (1024-dim)으로 인덱싱 후 스키마만 768로 변경됨 (DB 재생성 없음)
- 실제 차원 확인: `SELECT vector_dims(embedding) FROM mer_insights WHERE embedding IS NOT NULL LIMIT 1`
- `EMBEDDING_DIM = 768` (settings.py)은 **실제 DB와 다름** — 믿지 말 것

### VertexEmbedder vs 실험용 임베더
- `VertexEmbedder` (Vertex AI `text-multilingual-embedding-002`) → **768-dim** — 현재 DB(1024-dim)와 호환 불가
- 검색 실험(`experiment.py`)은 `intfloat/multilingual-e5-large` (1024-dim) 사용 — DB와 일치
- 프로덕션 재인덱싱 전까지 벡터 검색은 반드시 1024-dim 쿼리 필요

### src/search/__init__.py eager import
```python
from src.search.hybrid import hybrid_search, HybridSearcher  # ← 이게 문제
```
`python -m src.search.experiment` 실행 시 `__init__.py`가 먼저 로드되면서
`hybrid.py` → `vertex_embedder.py` → `config/settings.py` 체인이 실행됨.
GCP 환경변수가 없으면 import 시점에 크래시. `GCP_PROJECT_ID`는 optional로 처리됨(`os.environ.get`).

### scripts 실행 경로
`scripts/` 하위 모듈은 **프로젝트 루트에서** 실행해야 함:
```bash
cd /path/to/mer-insight-pipeline
python scripts/expand_eval_dataset.py  # ✓
```

## 아키텍처 요약

```
src/
├── agent/          # LLM 에이전트 루프 (while + tool_use, max_iterations=5)
├── search/         # 하이브리드 검색
│   ├── bm25_index.py     # kiwipiepy 형태소 분석 + rank-bm25
│   ├── vector_index.py   # pgvector HNSW 래퍼 (1024-dim)
│   ├── hybrid.py         # RRF 융합 (프로덕션 α=0.4, 실험 최적 α=0.6)
│   └── experiment.py     # ablation 실험 (multilingual-e5-large)
├── extract/        # Claude Batch API 인사이트 추출 + Vertex AI 임베딩
├── pipeline/       # 이벤트 디스패처, 모니터, 컨텍스트 어셈블러
├── guard/          # 환각 방지 가드 (GROUNDED / UNGROUNDED / UNSUPPORTED)
├── eval/           # LLM 심판 평가 파이프라인
├── observability/  # Tracer (비용·지연 추적 → PostgreSQL)
└── delivery/       # 텔레그램 2티어 전송
```

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `TELEGRAM_BOT_TOKEN` | 전송 시 | 텔레그램 봇 토큰 |
| `GCP_PROJECT_ID` | 선택 | Vertex AI 임베딩 (없으면 실험 fallback) |
| `FRED_API_KEY` | 선택 | FRED 경제 데이터 |
| `BOK_API_KEY` | 선택 | 한국은행 ECOS |

## 검색 실험 결과 (현재 기준)

- 데이터셋: `eval_data/gold_extended.json` (200개 쿼리)
- 임베딩: `intfloat/multilingual-e5-large` (1024-dim)
- **최적 α=0.6**: P@5=0.199, R@5=0.995, MRR=0.938
- 프로덕션 α=0.4: P@5=0.190, R@5=0.950, MRR=0.913
- 순수 벡터(α=0.0) MRR < 순수 BM25(α=1.0) — 한국 금융 텍스트는 키워드 매칭 비중 높음
