# mer-insight-pipeline

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg?token=WEE9EGD2QP)](https://codecov.io/gh/11e3/mer-insight-pipeline)

**mer-insight-pipeline**은 비정형 한국어 경제 블로그 글을 구조화된 시간축 예측으로 변환하고, 실제로 맞았는지 추적하는 시스템입니다.

한국어 경제 해설에는 종목 코드도, 날짜도, 확신도도 없습니다. 자연어에서 검증 가능한 예측을 추출하고, 시간 범위를 부여하고, 실제 사건과 대조해 사후 검증하는 것은 기성 도구로 해결되지 않는 NLP + 정보 검색 문제입니다. 이 파이프라인은 1인 개발로 설계·구현하여 운영 중입니다.

[메르(ranto28)](https://blog.naver.com/ranto28)의 경제 블로그를 모니터링하고, Claude Batch API로 예측을 추출하며, 각 예측을 실제 결과와 대조 검증합니다 — 현재 5,020건 추적 중, 659건 자동 검증 완료 (적중률 72.8%), 54,461건 뉴스 헤드라인 DB. 검색은 PostgreSQL 기반 하이브리드 BM25 + pgvector (25,090개 인덱싱된 인사이트, RRF 융합 α=0.6)로 벡터 DB 벤더 종속 없이 구현했습니다.

[English README](README_EN.md) · **[📊 라이브 대시보드](https://mer-insight-pipeline.streamlit.app/)**

### 직접 구현한 것 (1인 개발)

- **풀 파이프라인**: 스크래핑 → LLM 추출 → 임베딩 → 하이브리드 검색 → 뉴스 DB → 검증 → 대시보드
- **데이터 기반 의사결정**: 검색 ablation 실험, 자동 검증 5가지 방식 비교 실험, 데이터 품질 감사까지 수행
- **비용 최적화**: web_search $175 → 뉴스 DB + Batch API $1.50로 99% 비용 절감 설계

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
        ED -->|2| NEWS[collect/news_collector<br>뉴스 헤드라인 수집]
        ED -->|3| PV[verify/verifier<br>내보내기 + 알림]
        MER --> C
        NEWS -->|15개 RSS 피드| NH[(news_headlines<br>41,000건)]
    end

    subgraph 검증 파이프라인
        NH -->|키워드 GIN 매칭| HM[headline_matcher]
        HM -->|매칭된 헤드라인| BATCH[Batch API<br>yes/no 판정]
        BATCH --> C
        PV -->|매칭 안 되는 건| EX[수동 검증<br>claude.ai]
        EX -->|import_manual_verdicts.py| C
    end

    subgraph 하이브리드 검색
        HS1[BM25<br>kiwipiepy] & HS2[pgvector<br>HNSW] -->|RRF α=0.6| HS3[HybridSearcher]
    end

    HS3 --> C
    C --> Q[Streamlit 대시보드]
    EV[eval/experiment.py<br>오프라인 ablation] -.-> HS3
```

---

## 예측 검증

메르 포스트에서 추출된 모든 `prediction` 타입 인사이트는 `mer_predictions`에 저장됩니다. 검증은 3계층으로 구성됩니다:

| 계층 | 검증 소스 | 비용 | 예상 커버리지 |
|------|----------|------|-------------|
| 1. 헤드라인 매칭 | news_headlines DB + Batch API | ~$1.50 / 5,000건 | ~40-50% |
| 2. 데이터 API | 환율/주가/금리 (무료 API) | $0 | +20-30% (예정) |
| 3. 수동 검증 | claude.ai (Opus + 웹 검색) | $20/월 구독 | 나머지 |

### 판정 기준

| 판정 | 조건 |
|------|------|
| `CORRECT` | 예측 내용이 근거에 의해 확인됨 |
| `INCORRECT` | 근거에 의해 예측 내용이 반박됨 |
| `PENDING` | 조건 미충족 또는 정보 부족 — `expected_date` 경과 시 재검증 |

### 뉴스 헤드라인 DB (자동 검증 인프라)

web_search API로 예측을 1건씩 검증하면 건당 $0.035 (5,000건 = $175). 이 비용을 99% 줄이기 위해 뉴스 헤드라인 DB를 구축했습니다.

**원리:** 대부분의 예측은 뉴스 헤드라인만 봐도 검증 가능합니다. "BOJ가 7월에 금리를 인상했다" → 헤드라인에 바로 나옴. 매일 헤드라인을 수집해두면, 검증 시 web_search 없이 DB에서 키워드 매칭만 하면 됩니다.

```
수집: Google News RSS 15개 피드 → feedparser → kiwipiepy 키워드 추출 → news_headlines (GIN 인덱스)
매칭: prediction_date+1일 ~ 오늘 범위에서 키워드 GIN 매칭 (MIN_OVERLAP=3, 불용어 필터)
판정: 매칭된 헤드라인 → Haiku verdict+reason만 반환, source_url은 최고 overlap 헤드라인에서 자동 할당
배치: scripts/ops/batch_verify.py create → status → apply (Batch API 50% 할인)
```

| 항목 | 수치 |
|------|------|
| 항목 | 수치 |
| 수집 소스 | Google News RSS 33개 피드 + Naver News API 14개 쿼리 |
| 수집 주기 | 매일 자동 (event_dispatcher 파이프라인 step 2) |
| 키워드 추출 | kiwipiepy (한국어 NNG/NNP/SL) + regex (영어), LLM 없음 |
| 인덱스 | PostgreSQL GIN on TEXT[] (키워드 배열) |
| 헤드라인 총량 | **54,461건** (Google 41,463 + Naver 12,998) |
| 일일 수집량 | ~1,500건 (RSS + Naver) |
| 수집 비용 | $0 (RSS 무료, Naver 무료 일 25,000건) |

### 예측 추출 형식 (신규)

기존 예측은 자연어 텍스트만 저장했으나, 자동 검증을 위해 구조화된 형식으로 변경했습니다:

```json
{
  "prediction": "예측 원문",
  "claim": "미국 기준금리가 2024년 내 인하된다",
  "search_keywords": ["Federal Reserve", "금리", "인하", "2024"],
  "expected_date": "2024-12-31",
  "direction": "down",
  "target_asset": "미국 기준금리"
}
```

- **claim**: yes/no로 답할 수 있는 명확한 명제
- **search_keywords**: 뉴스 DB 키워드 매칭용
- **expected_date**: 검증 가능 시점

기존 5,020건은 구형식 (claim 없음). 신규 글부터 새 형식 적용. 구형식 예측은 `headline_matcher`가 `prediction_text`에서 키워드를 실시간 추출하여 매칭.

### 자동 검증 실험 결과

77건의 예측을 대상으로 API 자동 검증과 수동(claude.ai) 검증을 비교하는 실험을 진행했습니다:

| 방식 | 일치율 | 판정 상반 | 비용/건 | 비고 |
|------|--------|-----------|---------|------|
| API만 (검색 없음) | 16.9% | 1건 | $0.01 | 80% PENDING — knowledge cutoff |
| API + Brave 원샷 검색 | 37.7% | 5건 | $0.02 | snippet 불충분 |
| API + 내장 web_search (Sonnet) | 30% | 2건 | $0.05 | 불안정 |
| API + 에이전틱 tool_use (Opus) | 40% | 3건 | $0.26 | 토큰 누적 비용 폭발 |
| API + web_search 1건씩 (Haiku) | 80% | 1건 | $0.035 | 5,000건 = $175 |
| **뉴스 DB + Batch API (설계)** | **TBD** | **TBD** | **$0.0003** | **5,000건 = ~$1.50** |
| **claude.ai 수동 (Opus)** | **기준** | **0건** | **$0** | **구독 $20/월** |

**Batch API 실전 결과 (3회 누적):**

| 항목 | 수치 |
|------|------|
| 매칭 대상 | 5,020건 중 1,276건 (27.7%) |
| 검증 완료 (CORRECT+INCORRECT) | 659건 |
| CORRECT | 480건 (적중률 72.8%) |
| INCORRECT | 179건 |
| PENDING | 4,361건 |
| **총 비용** | **~$2.50** (건당 $0.0038) |
| web_search 대비 | **89% 절감** ($0.035 → $0.0038) |

**근본 원인 — 비용:** web_search 결과가 input 토큰으로 과금 (건당 30K~100K 토큰). 뉴스 DB 방식은 헤드라인 텍스트(~100 토큰)만 넣으므로 비용이 99% 절감됩니다.

### 데이터 품질 감사

초기 배치 검증(50~수백 건 단위)에서 LLM JSON 출력의 ID-verdict 밀림으로 인한 DB 오염을 발견했습니다. 50건 블라인드 재검증 감사를 수행하여 오염 규모를 정량화했습니다.

| 감사 항목 | 결과 |
|-----------|------|
| 샘플 크기 | 50건 (랜덤 추출, 블라인드) |
| 일치 (verdict 동일) | 32건 (64%) |
| 판정 상반 (CORRECT↔INCORRECT) | 7건 (14%) |
| PENDING 전환 | 11건 (22%) |
| 95% 신뢰구간 (Wilson) | **오염률 24.1% ~ 49.9%** |

**대응:** 전체 verdict 리셋 (백업 보존) → 소규모 배치(20건) + source_url 필수로 전환.

### 현재 현황

| 상태 | 건수 |
|------|------|
| CORRECT | 480 |
| INCORRECT | 179 |
| PENDING | 4,361 |
| 뉴스 헤드라인 DB | 54,461 |
| **합계 예측** | **5,020** |

---

## 검색 인프라

하이브리드 BM25 + 벡터 검색은 25,090개 인덱싱된 인사이트에서 관련 문맥을 검색합니다. **관련 문서 하나를 놓치면 불완전한 근거로 판정이 내려질 수 있기 때문에**, Recall이 Precision보다 중요합니다.

**알파 ablation** — N=200 쿼리, K=5:

| α (BM25 가중치) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| α=0.0 (vector-only) | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 (BM25-only) | 0.196 | 0.980 | 0.935 |

프로덕션 기본값: **α=0.6** — 완전한 Recall(1.000)을 달성하는 유일한 설정.

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| LLM (추출) | Claude Sonnet 4.6 / Haiku 4.5 (Batch API) |
| LLM (검증) | claude.ai (Opus 4.6, 수동) → Batch API (계획) |
| 임베딩 | `intfloat/multilingual-e5-large` (1024차원, 로컬) |
| 벡터 DB | PostgreSQL 16 + pgvector (HNSW 인덱스) |
| 키워드 검색 | rank-bm25 + kiwipiepy (한국어 형태소 분석) |
| 뉴스 수집 | Google News RSS + feedparser |
| 키워드 추출 | kiwipiepy (한국어) + regex (영어) |
| 하이브리드 융합 | Reciprocal Rank Fusion (RRF, α=0.6) |
| 스케줄러 | APScheduler / GCP Cloud Run Job |
| 대시보드 | Streamlit (Cloud 배포) |

---

## 지표

| 지표 | 값 |
|------|---|
| 처리된 포스트 | 2,223개 |
| 추출된 인사이트 | 25,090개 |
| 추적 중인 예측 | 5,020건 |
| 뉴스 헤드라인 | 54,461건 (Google 41,463 + Naver 12,998) |
| 검증 완료 | 659건 (적중률 72.8%) |
| 인사이트 유형 | 4가지 (rule, prediction, evaluation, macro_view) |
| 임베딩 차원 | 1024 |
| 테스트 커버리지 | 90%+ (235 tests) |

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

# 5. 뉴스 헤드라인 백필 (2022~현재)
PYTHONPATH=. python scripts/ops/backfill_news.py --start 2022-01 --end 2026-03

# 6. 파이프라인 1회 실행 또는 일일 스케줄러
python -m scripts.run_job                 # 1회 실행
python -m src.pipeline.event_dispatcher   # 매일 01:00 스케줄러
```

### 일일 파이프라인

매일 01:00 (KST) Cloud Scheduler 또는 APScheduler로 실행:

1. 메르 블로그 — 신규 글 확인, 예측 추출
2. 뉴스 헤드라인 — Google News RSS 15개 피드 수집
3. 예측 내보내기 — 검증 가능한 예측 자동 내보내기 + 텔레그램 알림

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

219개 테스트, 90%+ 커버리지. 단위 테스트는 데이터베이스 없이 실행 가능.

---

## 비용

| 항목 | 비용 | 빈도 |
|------|------|------|
| 인사이트 추출 (Sonnet/Haiku) | ~$0.01/글 | 신규 글 감지 시 |
| 뉴스 헤드라인 수집 (RSS) | $0 | 매일 자동 |
| 헤드라인 매칭 검증 (Batch API) | ~$0.0003/건 | 검증 시 (계획) |
| 수동 검증 (claude.ai) | $20/월 구독 | 매칭 안 되는 건 |
| 임베딩 (로컬) | $0 | 신규 인사이트당 |

**월 예상 비용 ≈ $2-5** (추출만). 검증은 뉴스 DB + Batch API로 전환 예정 (~$1.50 / 전체).

---

## 환경 변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `TELEGRAM_BOT_TOKEN` | 선택 | 텔레그램 알림 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 선택 | 텔레그램 채팅 ID |
| `NAVER_CLIENT_ID` | 선택 | 네이버 뉴스 API (뉴스 수집 보강) |
| `NAVER_CLIENT_SECRET` | 선택 | 네이버 뉴스 API |

---

## 프로젝트 구조

```
mer-insight-pipeline/
├── src/
│   ├── config/                     # 설정
│   │   ├── settings.py             # 전체 설정, .env에서 로드
│   │   └── prompts.py              # Claude 추출 프롬프트 (claim, search_keywords 포함)
│   ├── db/                         # 공유 DB 유틸리티
│   │   └── connection.py           # connect(), get_pool() context managers
│   ├── embed/                      # 임베딩
│   │   ├── protocol.py             # Embedder Protocol 인터페이스
│   │   ├── local.py                # multilingual-e5-large 1024차원 (기본)
│   │   ├── factory.py              # get_embedder() 팩토리 + vec_str()
│   │   └── backfill.py             # NULL 임베딩 일괄 생성
│   ├── extract/                    # 인사이트 추출
│   │   ├── batch_api.py            # Claude Batch API 오케스트레이션
│   │   ├── parse_results.py        # 배치 결과 JSONL → DB
│   │   └── realtime.py             # 실시간 인사이트 추출 (expected_date 저장)
│   ├── collect/                    # 데이터 수집
│   │   ├── mer_monitor.py          # 블로그 RSS 감시
│   │   ├── news_collector.py       # 뉴스 헤드라인 수집 (RSS 33피드 + Naver API 14쿼리)
│   │   ├── feeds.py                # Google News RSS 피드 정의
│   │   ├── keyword_extractor.py    # 키워드 추출 (kiwipiepy + regex)
│   │   ├── posts.py                # JSON → mer_posts 일괄 적재
│   │   └── date_parser.py          # 한국어 날짜 문자열 파서
│   ├── verify/                     # 예측 검증
│   │   ├── verifier.py             # 검증 대기 예측 내보내기 + 텔레그램 알림
│   │   ├── headline_matcher.py     # 예측 ↔ 헤드라인 키워드 GIN 매칭
│   │   └── prompt.py               # 상수 (배치 크기 20건)
│   ├── search/                     # 하이브리드 검색
│   │   ├── bm25_index.py           # BM25 + kiwipiepy + pickle 캐시
│   │   ├── vector_index.py         # pgvector HNSW 래퍼 (1024차원)
│   │   └── hybrid.py               # RRF 융합 (α=0.6)
│   ├── eval/                       # 평가 파이프라인
│   │   ├── experiment.py           # Ablation: 벡터 vs BM25 vs 하이브리드
│   │   ├── eval_runner.py          # 메인 러너
│   │   ├── metrics.py              # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py            # LLM-as-judge (Claude Sonnet)
│   │   └── report.py               # 마크다운 + JSON 리포트
│   ├── pipeline/                   # 오케스트레이션
│   │   └── event_dispatcher.py     # APScheduler / Cloud Run Job
│   └── dashboard/                  # Streamlit 대시보드
│       ├── app.py                  # 레이아웃 + 렌더링
│       ├── queries.py              # DB 쿼리 함수 (psycopg2)
│       └── topics.py               # 주제 분류 (TOPIC_KEYWORDS)
├── scripts/
│   ├── run_job.py                  # Cloud Run Job 진입점
│   ├── run_batch.py                # 배치 추출 오케스트레이터
│   ├── naver_blog_scraper.py       # 블로그 스크래퍼
│   ├── migrate_news_headlines.sql  # 뉴스 DB 마이그레이션
│   ├── ops/                        # 데이터 운영 스크립트
│   │   ├── backfill_news.py        # 뉴스 헤드라인 역사 백필
│   │   ├── import_manual_verdicts.py  # 수동 검증 결과 가져오기
│   │   ├── verify_pipeline/        # 3단계 압축 검증 파이프라인
│   │   └── ...                     # 기타 운영 스크립트
│   └── eval/                       # 평가 스크립트
├── data/
│   ├── verify_pipeline/            # 검증 파이프라인 중간 결과
│   ├── manual_verify/              # 수동 검증 결과 (round별)
│   └── audit/                      # 데이터 품질 감사 결과
├── eval_data/
│   └── gold_extended.json          # 골드 데이터셋: 200개 쿼리
├── docker-compose.yml
├── Dockerfile
├── requirements.txt                # Streamlit Cloud용 (경량)
└── requirements-full.txt           # 전체 의존성
```

---

## 라이선스

MIT
