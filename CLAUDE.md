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

### 임베딩 차원 (1024-dim 통일 완료)
- DB, `init_db.sql`, `settings.py` 모두 **1024-dim** (`intfloat/multilingual-e5-large`)으로 통일
- 기본 임베더: `LocalEmbedder` (`src/extract/local_embedder.py`) — GCP 불필요
- `VertexEmbedder` (768-dim)는 GCP_PROJECT_ID 설정 시에만 사용 (DB 재인덱싱 필요)
- `get_embedder()` 팩토리 함수가 환경에 따라 자동 선택

### src/search/__init__.py lazy import (수정 완료)
`__getattr__` 기반 lazy import로 변경 — GCP 환경변수 없이도 `src.search.experiment` 등 실행 가능.

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
