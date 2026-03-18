# mer-insight-pipeline

**mer-insight-pipeline**은 [메르(ranto28)](https://blog.naver.com/ranto28)의 한국 경제 블로그 포스트에서 금융 예측을 자동으로 추적하고 검증하는 파이프라인입니다.

Claude Batch API로 예측을 추출하고, Claude Opus가 자동 심판으로 매일 각 예측을 검증합니다 — 현재 5,368건 추적, 4,219건 검증 완료. 검색은 PostgreSQL 기반 하이브리드 BM25 + pgvector (25,090개 인덱싱된 인사이트, RRF 융합 α=0.6)로 벡터 DB 벤더 종속 없이 구현했습니다.

[English README](README.md)

---

## 아키텍처

```mermaid
flowchart TD
    subgraph 배치 추출
        A[네이버 블로그<br>2,223개 포스트] -->|스크래핑| B[parse_results.py]
        B -->|규칙 / 예측<br>평가 / 매크로뷰| C[(PostgreSQL<br>+ pgvector)]
        C --> EMB[embed/local.py<br>1024차원 벡터]
        EMB --> C
    end

    subgraph "일일 파이프라인 (01:00)"
        ED[event_dispatcher.py] -->|1| MER[collect/mer_monitor<br>신규 글 수집]
        ED -->|2| PV[verify/verifier<br>Claude Opus 심판]
        MER --> C
        PV -->|CORRECT / INCORRECT / PENDING| C
    end

    subgraph 하이브리드 검색
        HS1[BM25<br>kiwipiepy] & HS2[pgvector<br>HNSW] -->|RRF α=0.6| HS3[HybridSearcher]
    end

    HS3 --> C

    C --> Q[Streamlit 대시보드]

    EV[eval/experiment.py<br>오프라인 ablation] -.-> HS3
```

---

## 예측 검증 파이프라인

메르 포스트에서 추출된 모든 `prediction` 타입 인사이트는 `mer_predictions`에 저장되고, 매일 Claude Opus가 자체 지식을 활용해 자동 검증합니다.

**판정 기준**

| 판정 | 조건 |
|------|------|
| `CORRECT` | Claude 지식으로 예측 내용이 확인됨 |
| `INCORRECT` | 근거에 의해 예측 내용이 반박됨 |
| `PENDING` | 조건 미충족 또는 정보 부족 — 다음날 재검증 |

만기 없이 확정될 때까지 큐에 유지. `BATCH_SIZE=60` 예측을 Opus에 전달하며, **프롬프트 캐싱** 적용 (캐시된 입력 비용 ~90% 절감).

**현재 현황**

| 상태 | 건수 |
|------|------|
| CORRECT | 3,706 |
| INCORRECT | 513 |
| PENDING | 1,149 |
| **합계** | **5,368** |

---

## 검색 인프라

하이브리드 BM25 + 벡터 검색이 평가 파이프라인의 검색 백본입니다.

쿼리 임베딩은 `intfloat/multilingual-e5-large` (1024차원) — 프로덕션 DB 인덱싱에 사용된 동일 모델.

**알파 ablation** — N=200 쿼리, K=5:

| α (BM25 가중치) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| **α=0.0** | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 | 0.196 | 0.980 | 0.935 |

프로덕션 기본값: **α=0.6** — 완전한 Recall(1.000)을 달성하는 유일한 설정.

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| LLM (추출) | `claude-sonnet-4-6` (Haiku 선택 가능: `--haiku`) |
| LLM (검증) | `claude-opus-4-6` |
| 배치 API | Anthropic Batch API |
| 임베딩 | `intfloat/multilingual-e5-large` (1024차원, 로컬) |
| 벡터 DB | PostgreSQL 16 + pgvector (HNSW 인덱스) |
| 키워드 검색 | rank-bm25 + kiwipiepy (한국어 형태소 분석) |
| 하이브리드 융합 | Reciprocal Rank Fusion (RRF, α=0.6) |
| 스케줄러 | APScheduler (로컬) / GCP Cloud Scheduler + Cloud Run Job |
| 대시보드 | Streamlit |

---

## 지표

| 지표 | 값 |
|------|---|
| 처리된 포스트 | 2,223개 |
| 추출된 인사이트 | 25,090개 |
| 추적 중인 예측 | 5,368건 |
| 검증 완료 | 4,219건 (78.6%) |
| 적중률 (CORRECT 비율) | 87.8% |
| 인사이트 유형 | 4가지 (rule, prediction, evaluation, macro_view) |
| 임베딩 차원 | 1024 |

---

## 설치

### 사전 요구사항

- Docker & Docker Compose
- Anthropic API 키

### 빠른 시작

```bash
# 1. 클론 및 설정
git clone https://github.com/11e3/mer-insight-pipeline.git
cd mer-insight-pipeline
cp .env.example .env        # API 키 입력

# 2. 데이터베이스 시작
docker compose up -d db

# 3. 배치 추출 실행 (포스트 → 인사이트)
python scripts/run_batch.py all

# 4. BM25 인덱스 캐시 빌드
python -m src.search.bm25_index

# 5. 파이프라인 1회 실행 또는 일일 스케줄러 시작
python -m scripts.run_job                 # 1회 실행
python -m src.pipeline.event_dispatcher   # 매일 01:00 스케줄러
```

### 일일 파이프라인

매일 01:00 (KST) Cloud Scheduler 또는 APScheduler로 실행:

1. 메르 블로그 — 신규 글 확인, 예측 추출
2. 예측 검증 — Claude Opus가 모든 미검증 예측 판정

### 대시보드

```bash
streamlit run src/dashboard/app.py
```

### 평가

```bash
python -m src.eval.eval_runner --mode retrieval_only
python -m src.eval.experiment --mode ablation --k 5
```

---

## 테스트

```bash
# 단위 테스트 (DB 불필요)
pytest tests/ -v

# 통합 테스트 (PostgreSQL 필요)
TEST_DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test \
  pytest tests/test_integration_dispatcher.py -v
```

단위 테스트는 데이터베이스 없이 실행 가능. 통합 테스트는 `TEST_DATABASE_URL` 환경변수 필요 — 미설정 시 자동 스킵.

---

## 비용

일일 검증은 모든 미검증 예측에 대해 Claude Opus를 실행하며, 다음과 같이 비용을 최적화합니다:

| 최적화 | 상세 |
|--------|------|
| 프롬프트 캐싱 | system prompt에 `cache_control` 적용 — 2번째 배치부터 input 비용 ~90% 절감 |
| 배치 크기 | `BATCH_SIZE=60` 예측을 1회 API 호출로 처리 |
| `max_tokens` | 8,192 (너무 작으면 JSON 응답 잘림 → 파싱 실패) |
| 한국어 토큰 배수 | 한국어 텍스트는 영어 대비 토큰 소비 2-3배 → 비용 견적 시 2배로 계산 |

**월 예상 비용 ≈ $10-30** (일일 파이프라인만, Opus 4.6 기준, 건당 ~$0.008). 프롬프트 캐싱으로 대부분의 input이 90% 할인된 read rate로 처리됩니다. Sonnet 배치 추출은 별도.

---

## 환경 변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `GCP_PROJECT_ID` | 선택 | GCP 프로젝트 ID (Vertex AI 임베딩용) |
| `GCP_LOCATION` | 선택 | Vertex AI 리전 (기본: us-central1) |

---

## 프로젝트 구조

```
mer-insight-pipeline/
├── src/
│   ├── config/                     # 설정
│   │   ├── settings.py             # 전체 설정, .env에서 로드
│   │   └── prompts.py              # Claude 추출 프롬프트
│   ├── db/                         # 공유 DB 유틸리티
│   │   └── connection.py           # connect(), get_pool() context managers
│   ├── embed/                      # 임베딩
│   │   ├── protocol.py             # Embedder Protocol 인터페이스
│   │   ├── local.py                # multilingual-e5-large 1024차원 (기본)
│   │   ├── vertex.py               # Vertex AI 768차원 (GCP 전용)
│   │   ├── factory.py              # get_embedder() 팩토리 + vec_str()
│   │   └── backfill.py             # NULL 임베딩 일괄 생성
│   ├── extract/                    # 인사이트 추출
│   │   ├── batch_api.py            # Claude Batch API 오케스트레이션
│   │   ├── parse_results.py        # 배치 결과 JSONL → DB
│   │   └── realtime.py             # 실시간 인사이트 추출 (Haiku)
│   ├── collect/                    # 데이터 수집
│   │   ├── mer_monitor.py          # 블로그 RSS 감시
│   │   ├── posts.py                # JSON → mer_posts 일괄 적재
│   │   └── date_parser.py          # 한국어 날짜 문자열 파서
│   ├── verify/                     # 예측 검증
│   │   ├── verifier.py             # PredictionVerifier (Claude Opus 배치)
│   │   └── prompt.py               # 시스템 프롬프트, 상수
│   ├── search/                     # 하이브리드 검색
│   │   ├── bm25_index.py           # BM25 + kiwipiepy + pickle 캐시
│   │   ├── vector_index.py         # pgvector HNSW 래퍼 (1024차원)
│   │   └── hybrid.py               # RRF 융합 (α=0.6)
│   ├── eval/                       # 평가 파이프라인
│   │   ├── experiment.py           # Ablation: 벡터 vs BM25 vs 하이브리드
│   │   ├── eval_runner.py          # 메인 러너 (--mode retrieval_only | full)
│   │   ├── metrics.py              # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py            # LLM-as-judge (Claude Sonnet)
│   │   └── report.py               # 마크다운 + JSON 리포트 생성기
│   ├── pipeline/                   # 오케스트레이션
│   │   └── event_dispatcher.py     # APScheduler / Cloud Run Job 진입점
│   └── dashboard/                  # Streamlit 대시보드
│       ├── app.py                  # 레이아웃 + 렌더링
│       ├── queries.py              # DB 쿼리 함수
│       └── topics.py               # 주제 분류 (TOPIC_KEYWORDS)
├── scripts/
│   ├── run_job.py                  # Cloud Run Job / 로컬 파이프라인 진입점
│   ├── run_batch.py                # 배치 추출 오케스트레이터
│   ├── reembed_all.py              # 전체 재임베딩 (모델 교체 시)
│   ├── naver_blog_scraper.py       # 블로그 스크래퍼
│   ├── ops/                        # 데이터 운영 스크립트
│   │   ├── export_*.py             # 예측 내보내기 (manual_verify, rounds)
│   │   ├── import_*.py             # 수동 검증 결과 가져오기
│   │   ├── fill_*.py               # 필드 채우기 (expected_date 등)
│   │   ├── migrate_predictions.py  # 일회성: insights → predictions 소급 적재
│   │   ├── populate_topics.py      # 주제 일괄 분류
│   │   ├── cluster_insights.py     # DBSCAN 중복 제거
│   │   └── regroup_by_topic.py     # 주제별 재분류
│   └── eval/                       # 평가 관련 스크립트
│       ├── expand_eval_dataset.py  # 골드 데이터셋 자동 확장
│       └── compare_judges.py       # 심판 비교
├── eval_data/
│   └── gold_extended.json          # 골드 데이터셋: 200개 쿼리 + 관련 인사이트 ID
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 라이선스

MIT
