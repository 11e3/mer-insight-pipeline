# mer-insight-pipeline

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg?token=WEE9EGD2QP)](https://codecov.io/gh/11e3/mer-insight-pipeline)

**mer-insight-pipeline**은 비정형 한국어 경제 블로그 글을 구조화된 시간축 예측으로 변환하고, 실제로 맞았는지 추적하는 시스템입니다.

한국어 경제 해설에는 종목 코드도, 날짜도, 확신도도 없습니다. 자연어에서 검증 가능한 예측을 추출하고, 시간 범위를 부여하고, 실제 사건과 대조해 사후 검증하는 것은 기성 도구로 해결되지 않는 NLP + 정보 검색 문제입니다. 이 파이프라인은 1인 개발로 설계·구현하여 6개월 이상 매일 프로덕션에서 운영 중입니다.

[메르(ranto28)](https://blog.naver.com/ranto28)의 경제 블로그를 모니터링하고, Claude Batch API로 예측을 추출하며, 각 예측을 실제 결과와 대조 검증합니다 — 현재 5,020건 추적, 재검증 진행 중. 검색은 PostgreSQL 기반 하이브리드 BM25 + pgvector (25,090개 인덱싱된 인사이트, RRF 융합 α=0.6)로 벡터 DB 벤더 종속 없이 구현했습니다.

[English README](README_EN.md) · **[📊 라이브 대시보드](https://mer-insight-pipeline.streamlit.app/)**

### 직접 구현한 것 (1인 개발)

- **풀 파이프라인**: 스크래핑 → LLM 추출 → 임베딩 → 하이브리드 검색 → 검증 → 대시보드
- **데이터 기반 의사결정**: 검색 ablation 실험 수행, 자동 검증 실험을 통해 수동 검증이 유일한 방법임을 데이터로 증명
- **프로덕션 운영**: 매일 Cloud Run Job 실행, 텔레그램 알림, 6개월 이상 무중단 운영

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
        ED -->|2| PV[verify/verifier<br>내보내기 + 알림]
        MER --> C
        PV -->|대기 예측 내보내기| EX[manual_verify/pending/]
        EX -->|claude.ai| MAN[수동 검증]
        MAN -->|import_manual_verdicts.py| C
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

메르 포스트에서 추출된 모든 `prediction` 타입 인사이트는 `mer_predictions`에 저장됩니다. `expected_date`가 지난 예측은 **수동 검증을 위해 자동으로 내보내지고**, 검증 완료 후 DB에 반영됩니다.

**판정 기준**

| 판정 | 조건 |
|------|------|
| `CORRECT` | 예측 내용이 근거에 의해 확인됨 |
| `INCORRECT` | 근거에 의해 예측 내용이 반박됨 |
| `PENDING` | 조건 미충족 또는 정보 부족 — `expected_date` 경과 시 재내보내기 |

### 워크플로우

1. **일일 파이프라인** — 검증 가능한 예측을 `data/manual_verify/pending/`에 자동 내보내기
2. **텔레그램 알림** — "검증 대기 N건" 알림 발송
3. **수동 검증** — claude.ai (Opus 4.6 + 웹 검색)에서 배치 검증
4. **결과 반영** — `python scripts/ops/import_manual_verdicts.py`

### 왜 자동화가 안 되는가?

77건의 예측을 대상으로 API 자동 검증과 수동(claude.ai) 검증을 비교하는 실험을 진행했습니다. **어떤 자동화 방식도 수용 가능한 정확도를 달성하지 못했습니다:**

| 방식 | 일치율 | 판정 상반 | 비용/건 | 비고 |
|------|--------|-----------|---------|------|
| API만 (검색 없음) | 16.9% | 1건 | $0.01 | 80% PENDING — knowledge cutoff 이후 사건 모름 |
| API + Brave 원샷 검색 | 37.7% | 5건 | $0.02 | snippet만으로 팩트체크 불가 |
| API + 내장 web_search (Sonnet) | 30% | 2건 | $0.05 | 검색 품질 개선되었으나 여전히 불안정 |
| API + 에이전틱 tool_use (Opus) | 40% | 3건 | $0.26 | 토큰 누적으로 비용 폭발 |
| API + web_search 1건씩 (Haiku) | 80% | 1건 | $0.035 | 정확도 향상, 그러나 4,050건 = ~$142 |
| API + web_search 1건씩 (Sonnet) | 80% | 1건 | $0.24 | Haiku와 동일 정확도, 7배 비용 |
| API + web_search 1건씩 (Opus) | 60% | 2건 | $0.86 | 가장 비싸고 파싱 실패도 발생 |
| **claude.ai 수동 (Opus)** | **기준** | **0건** | **$0** | **구독 $20/월, 무제한** |

**근본 원인 1 — 비용:** 예측 검증은 웹 검색이 필수인데, 내장 web_search의 검색 결과가 input 토큰으로 과금됩니다 (건당 30K~100K 토큰). 1건씩 처리해도 Haiku 기준 $0.035/건으로, 전체 재검증 시 ~$142. claude.ai 구독($20/월) 대비 7배.

**근본 원인 2 — 배치 검증 시 ID-reason 밀림:** 100건 배치로 검증하면 JSON 출력에서 ID와 reason이 뒤바뀌는 현상 발생 → DB 오염. grouped/ 파일에서 776건의 verdict 충돌, 24건의 명백한 주제 불일치(유가 예측에 딥시크 근거 등) 확인. 1건씩 처리하면 해결되지만 비용 문제로 비현실적.

**결론:** claude.ai를 통한 수동 검증만이 비용과 정확도 모두에서 유일하게 실용적인 방법입니다. 파이프라인은 그 외 모든 것(내보내기, 배치 분할, 알림, 결과 반영)을 자동화합니다. 배치 크기는 20건으로 제한하여 ID 밀림을 방지하고, source_url 필수로 검증 근거를 추적합니다.

### 데이터 품질 감사

초기 배치 검증(50~수백 건 단위)에서 LLM JSON 출력의 ID-verdict 밀림으로 인한 오염을 발견했습니다.
50건 블라인드 재검증 감사 결과 **오염률 36%** (95% CI: 24-50%)를 확인하고,
전체 verdict를 리셋한 뒤 소규모 배치(20건) + 소스 링크 필수 방식으로 전환했습니다.

| 감사 항목 | 결과 |
|-----------|------|
| 샘플 크기 | 50건 (랜덤 추출, 블라인드) |
| 일치 (verdict 동일) | 32건 (64%) |
| 판정 상반 (CORRECT↔INCORRECT) | 7건 (14%) |
| PENDING 전환 | 11건 (22%) |
| 95% 신뢰구간 (Wilson) | 오염률 24.1% ~ 49.9% |

### 3단계 압축 검증 파이프라인

5,000건을 1건씩 검증하면 ~$142(Haiku)이므로, 예측을 주제별로 묶고 고유 검증 포인트로 압축하여 검색 횟수를 줄이는 파이프라인을 설계했습니다.

```
5,020 predictions → 16개 주제 분류 → 223개 검증 포인트 추출 → web_search 판정 → 302건 매핑
```

| 단계 | 동작 | API 호출 | 비용 |
|------|------|---------|------|
| Stage 1 | 주제 분류 (Haiku, no search) | 51회 | $0.66 |
| Stage 2 | 검증 포인트 추출 (Haiku, no search) | 31회 | $1.22 |
| Stage 3 | 검색 + 판정 (Haiku + web_search) | 223회 | $10.21 |
| Stage 4 | 개별 예측에 매핑 (로컬) | 0 | $0 |
| web_search 과금 | ~446회 × ~$0.02 | - | ~$9 |
| **합계** | | **305회** | **~$21** |

**결과:** 223개 검증 포인트 중 CONFIRMED 136, DENIED 61, UNKNOWN 26. 302건 예측에 draft verdict 매핑 (CORRECT 178, INCORRECT 89, PENDING 35).

**한계:** Stage 2에서 200건 배치가 Haiku의 출력 한계를 초과하여 11개 주제(4,718건)가 누락됨. 배치 크기 축소(200→50)로 해결 가능하나 추가 예산 필요.

**현재 현황**

| 상태 | 건수 |
|------|------|
| Draft CORRECT | 178 |
| Draft INCORRECT | 89 |
| PENDING | 4,753 |
| **합계** | **5,020** |

*Draft verdict는 사람 검토 후 DB 반영 예정. source_url 필수.*

---

## 검색 인프라

하이브리드 BM25 + 벡터 검색은 검증 파이프라인에 문맥을 공급하는 검색 레이어입니다. 예측의 검증 시점이 되면 25,090개 인덱싱된 문서에서 가장 관련도 높은 인사이트와 맥락을 검색합니다 — **관련 문서 하나를 놓치면 불완전한 근거로 판정이 내려질 수 있기 때문에**, 이 파이프라인에서는 랭킹 정밀도보다 Recall이 더 중요합니다.

쿼리 임베딩은 `intfloat/multilingual-e5-large` (1024차원) — 프로덕션 DB 인덱싱에 사용된 동일 모델.

**알파 ablation** — N=200 쿼리, K=5:

| α (BM25 가중치) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| **α=0.0** | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 | 0.196 | 0.980 | 0.935 |

프로덕션 기본값: **α=0.6** — 완전한 Recall(1.000)을 달성하는 유일한 설정. Vector-only(α=0.0)가 MRR은 가장 높지만 관련 문서 0.5%를 놓칩니다. 하나의 누락된 사실이 판정을 뒤집을 수 있는 검증 파이프라인에서 이 차이는 무시할 수 없습니다.

---

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| LLM (추출) | `claude-sonnet-4-6` (Haiku 선택 가능: `--haiku`) |
| LLM (검증) | claude.ai (Opus 4.6, 수동) |
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
| 추적 중인 예측 | 5,020건 |
| 검증 완료 | 재검증 진행 중 (리셋 후) |
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
2. 예측 내보내기 — 검증 가능한 예측 자동 내보내기 + 텔레그램 알림

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

검증은 claude.ai 구독(~$20/월)으로 수동 진행. API 비용은 신규 글 감지 시 실시간 추출에만 발생합니다.

| 항목 | 비용 | 빈도 |
|------|------|------|
| 인사이트 추출 (Sonnet) | ~$0.01/글 | 신규 글 감지 시 |
| 예측 검증 | $0 (claude.ai 구독) | 주간 배치 |
| 임베딩 (로컬) | $0 | 신규 인사이트당 |

**월 예상 비용 ≈ $2-5** (추출만). 검증은 claude.ai 구독으로 처리.

---

## 환경 변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | ✓ | PostgreSQL 연결 문자열 |
| `ANTHROPIC_API_KEY` | ✓ | Claude API 키 |
| `TELEGRAM_BOT_TOKEN` | 선택 | 텔레그램 알림 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 선택 | 텔레그램 채팅 ID |
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
│   │   └── realtime.py             # 실시간 인사이트 추출
│   ├── collect/                    # 데이터 수집
│   │   ├── mer_monitor.py          # 블로그 RSS 감시
│   │   ├── posts.py                # JSON → mer_posts 일괄 적재
│   │   └── date_parser.py          # 한국어 날짜 문자열 파서
│   ├── verify/                     # 예측 검증
│   │   ├── verifier.py             # 검증 대기 예측 내보내기 + 텔레그램 알림
│   │   └── prompt.py               # 상수 (배치 크기)
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
