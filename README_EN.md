# mer-insight-pipeline

[![CI](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml/badge.svg)](https://github.com/11e3/mer-insight-pipeline/actions/workflows/update-readme.yml)
[![codecov](https://codecov.io/gh/11e3/mer-insight-pipeline/graph/badge.svg?token=WEE9EGD2QP)](https://codecov.io/gh/11e3/mer-insight-pipeline)

**mer-insight-pipeline** turns unstructured Korean financial blog prose into structured, time-bound predictions — then tracks whether they actually come true.

Korean economic commentary doesn't come with tickers, dates, or confidence levels. Extracting verifiable predictions from natural language, assigning temporal bounds, and fact-checking outcomes against real-world events is a non-trivial NLP + information retrieval problem that no off-the-shelf tool solves. This pipeline is a solo-built, end-to-end system.

The pipeline monitors [Mer (ranto28)](https://blog.naver.com/ranto28)'s finance blog, extracts predictions via Claude Batch API, and verifies each against real outcomes — currently tracking 5,020 predictions, 41,000 news headlines DB built, re-verification in progress. Retrieval is powered by hybrid BM25 + pgvector search (25,090 indexed insights, RRF fusion at α=0.6) on PostgreSQL with no vector-DB vendor lock-in.

[한국어 README](README.md) · **[📊 Live Dashboard](https://mer-insight-pipeline.streamlit.app/)**

### What I Built (Solo)

- **Full pipeline**: scraping → LLM extraction → embedding → hybrid search → news DB → verification → dashboard
- **Data-driven decisions**: search ablation experiments, 5-way automated verification comparison, data quality audit
- **Cost optimization**: web_search $175 → news DB + Batch API $1.50 — 99% cost reduction by design

---

## Architecture

```mermaid
flowchart TD
    subgraph Batch Extraction
        A[Naver Blog<br>2,223 posts] -->|scrape| B[parse_results.py]
        B -->|rules / predictions<br>evaluations / macro_views| C[(PostgreSQL<br>+ pgvector)]
        C --> EMB[embed/local.py<br>1024-dim vectors]
        EMB --> C
    end

    subgraph "Daily Pipeline (01:00)"
        ED[event_dispatcher.py] -->|1| MER[collect/mer_monitor<br>new posts]
        ED -->|2| NEWS[collect/news_collector<br>news headlines]
        ED -->|3| PV[verify/verifier<br>export + notify]
        MER --> C
        NEWS -->|15 RSS feeds| NH[(news_headlines<br>41,000)]
    end

    subgraph Verification Pipeline
        NH -->|keyword GIN matching| HM[headline_matcher]
        HM -->|matched headlines| BATCH[Batch API<br>yes/no verdict]
        BATCH --> C
        PV -->|unmatched| EX[Manual verification<br>claude.ai]
        EX -->|import_manual_verdicts.py| C
    end

    subgraph Hybrid Search
        HS1[BM25<br>kiwipiepy] & HS2[pgvector<br>HNSW] -->|RRF α=0.6| HS3[HybridSearcher]
    end

    HS3 --> C
    C --> Q[Streamlit Dashboard]
    EV[eval/experiment.py<br>offline ablation] -.-> HS3
```

---

## Prediction Verification

Every `prediction`-type insight extracted from Mer's posts is stored in `mer_predictions`. Verification is structured in 3 tiers:

| Tier | Verification Source | Cost | Expected Coverage |
|------|-------------------|------|-------------------|
| 1. Headline matching | news_headlines DB + Batch API | ~$1.50 / 5,000 | ~40-50% |
| 2. Data APIs | FX/stock/rate APIs (free) | $0 | +20-30% (planned) |
| 3. Manual verification | claude.ai (Opus + web search) | $20/mo subscription | remainder |

### Verdict Criteria

| Verdict | Condition |
|---------|-----------|
| `CORRECT` | Predicted outcome confirmed with evidence |
| `INCORRECT` | Predicted outcome contradicted by evidence |
| `PENDING` | Condition not yet met or insufficient info — re-verified after `expected_date` passes |

### News Headlines DB (Automated Verification Infrastructure)

Verifying predictions one-by-one with web_search API costs $0.035/prediction (5,000 = $175). To cut this cost by 99%, we built a news headlines database.

**Principle:** Most predictions can be verified from news headlines alone. "BOJ raised rates in July" → appears directly in headlines. By collecting headlines daily, verification only requires keyword matching against the DB — no web_search needed.

```
Collection: Google News RSS 15 feeds → feedparser → kiwipiepy keyword extraction → news_headlines (GIN index)
Matching:   prediction.keywords ↔ headlines.keywords (±30 days) → matched headlines + claim → Batch API yes/no
```

| Item | Value |
|------|-------|
| Sources | Google News RSS 15 feeds (10 Korean + 5 English) |
| Frequency | Daily automatic (event_dispatcher pipeline step 2) |
| Keyword extraction | kiwipiepy (Korean NNG/NNP/SL) + regex (English), no LLM |
| Index | PostgreSQL GIN on TEXT[] (keyword array) |
| Backfill completed | 2022-01 to 2026-03, **41,233 headlines** |
| Daily collection | ~240 headlines |
| Collection cost | $0 (RSS free) |
| Storage | ~25MB |

**Headlines by year:**

| Year | Count |
|------|-------|
| 2022 | 7,768 |
| 2023 | 8,664 |
| 2024 | 9,957 |
| 2025 | 14,573 |
| 2026 | 271 |
| **Total** | **41,233** |

### Prediction Extraction Format (New)

Previous predictions stored only natural language text. For automated verification, we switched to a structured format:

```json
{
  "prediction": "Original prediction text",
  "claim": "US Fed will cut rates before end of 2024",
  "search_keywords": ["Federal Reserve", "interest rate", "cut", "2024"],
  "expected_date": "2024-12-31",
  "direction": "down",
  "target_asset": "US federal funds rate"
}
```

- **claim**: A clear proposition answerable with yes/no
- **search_keywords**: For news DB keyword matching
- **expected_date**: When the prediction becomes verifiable

Existing 5,020 predictions use the old format (no claim). New posts use the structured format. For old predictions, `headline_matcher` extracts keywords from `prediction_text` at runtime.

### Automated Verification Experiments

We compared 5 automated API verification approaches against manual claude.ai verification on 77 predictions:

| Approach | Match Rate | Verdict Flips | Cost/pred | Notes |
|----------|-----------|---------------|-----------|-------|
| API only (no search) | 16.9% | 1 | $0.01 | 80% PENDING — knowledge cutoff |
| API + one-shot Brave Search | 37.7% | 5 | $0.02 | Snippets insufficient |
| API + built-in web_search (Sonnet) | 30% | 2 | $0.05 | Unreliable |
| API + agentic tool_use (Opus) | 40% | 3 | $0.26 | Token accumulation cost explosion |
| API + web_search per-prediction (Haiku) | 80% | 1 | $0.035 | 5,000 = $175 |
| **News DB + Batch API (design)** | **TBD** | **TBD** | **$0.0003** | **5,000 = ~$1.50** |
| **claude.ai manual (Opus)** | **baseline** | **0** | **$0** | **$20/mo subscription** |

**Root cause — cost:** web_search results are billed as input tokens (30K–100K tokens per prediction). The news DB approach feeds only headline text (~100 tokens), cutting cost by 99%.

### Data Quality Audit

Large-batch verification (50–100+ predictions) caused ID-verdict misalignment in LLM JSON output, corrupting the database. A blind audit of 50 random samples quantified the contamination:

| Audit Item | Result |
|------------|--------|
| Sample size | 50 (random, blind) |
| Match (same verdict) | 32 (64%) |
| Verdict flip (CORRECT↔INCORRECT) | 7 (14%) |
| Changed to PENDING | 11 (22%) |
| 95% CI (Wilson) | **Contamination 24.1%–49.9%** |

**Response:** Full verdict reset (backup preserved) → switched to small batches (20) + mandatory source_url.

### Current Status

| Status | Count |
|--------|-------|
| PENDING (awaiting re-verification) | 5,020 |
| News headlines DB | 41,233 |
| **Total predictions** | **5,020** |

---

## Search Infrastructure

Hybrid BM25 + vector search retrieves relevant context from 25,090 indexed insights. **Missing a relevant document means incomplete evidence for verdict decisions**, so Recall matters more than Precision.

**Alpha ablation** — N=200 queries, K=5:

| α (BM25 weight) | Precision@5 | Recall@5 | MRR |
|----------------|-------------|----------|-----|
| α=0.0 (vector-only) | 0.199 | 0.995 | **0.995** |
| **α=0.6** ★ | **0.200** | **1.000** | 0.968 |
| α=1.0 (BM25-only) | 0.196 | 0.980 | 0.935 |

Production default: **α=0.6** — the only setting that achieves perfect Recall (1.000).

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| LLM (extraction) | Claude Sonnet 4.6 / Haiku 4.5 (Batch API) |
| LLM (verification) | claude.ai (Opus 4.6, manual) → Batch API (planned) |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim, local) |
| Vector DB | PostgreSQL 16 + pgvector (HNSW index) |
| Keyword Search | rank-bm25 + kiwipiepy (Korean morphological analysis) |
| News collection | Google News RSS + feedparser |
| Keyword extraction | kiwipiepy (Korean) + regex (English) |
| Hybrid Fusion | Reciprocal Rank Fusion (RRF, α=0.6) |
| Scheduler | APScheduler / GCP Cloud Run Job |
| Dashboard | Streamlit (Cloud deployment) |

---

## Metrics

| Metric | Value |
|--------|-------|
| Processed posts | 2,223 |
| Extracted insights | 25,090 |
| Tracked predictions | 5,020 |
| News headlines | 41,233 (2022–2026) |
| Verification status | Re-verification in progress |
| Insight types | 4 (rule, prediction, evaluation, macro_view) |
| Embedding dimensions | 1024 |
| Test coverage | 90%+ (219 tests) |

---

## Setup

### Prerequisites

- Docker & Docker Compose
- Anthropic API key

### Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/11e3/mer-insight-pipeline.git
cd mer-insight-pipeline
cp .env.example .env        # fill in your API keys

# 2. Start the database
docker compose up -d db

# 3. Run batch extraction (posts → insights)
python scripts/run_batch.py all

# 4. Build BM25 index cache
python -m src.search.bm25_index

# 5. Backfill news headlines (2022–present)
PYTHONPATH=. python scripts/ops/backfill_news.py --start 2022-01 --end 2026-03

# 6. Run pipeline once or start daily scheduler
python -m scripts.run_job                 # run once
python -m src.pipeline.event_dispatcher   # daily 01:00 scheduler
```

### Daily Pipeline

Runs once daily at 01:00 (KST) via Cloud Scheduler or APScheduler:

1. Mer blog — check for new posts, extract predictions
2. News headlines — collect from 15 Google News RSS feeds
3. Prediction export — export verifiable predictions + Telegram alert

### Dashboard

```bash
streamlit run src/dashboard/app.py
```

### Eval

```bash
python -m src.eval.eval_runner --mode retrieval_only
python -m src.eval.experiment --mode ablation --k 5
```

---

## Tests

```bash
# Unit tests (no DB required)
pytest tests/ -v

# Integration tests (requires PostgreSQL)
TEST_DATABASE_URL=postgresql://mer:pass@localhost:5432/mer_test \
  pytest tests/test_integration_dispatcher.py -v
```

219 tests, 90%+ coverage. Unit tests run without a database.

---

## Cost

| Component | Cost | Frequency |
|-----------|------|-----------|
| Insight extraction (Sonnet/Haiku) | ~$0.01/post | Per new post |
| News headline collection (RSS) | $0 | Daily automatic |
| Headline matching verification (Batch API) | ~$0.0003/pred | On verification (planned) |
| Manual verification (claude.ai) | $20/mo subscription | Unmatched predictions |
| Embedding (local) | $0 | Per new insight |

**Estimated monthly cost ≈ $2-5** (extraction only). Verification transitioning to news DB + Batch API (~$1.50 total).

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✓ | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | ✓ | Claude API key |
| `TELEGRAM_BOT_TOKEN` | optional | Telegram notification bot token |
| `TELEGRAM_CHAT_ID` | optional | Telegram chat ID |
| `NAVER_CLIENT_ID` | optional | Naver News API (supplementary collection) |
| `NAVER_CLIENT_SECRET` | optional | Naver News API |

---

## Project Structure

```
mer-insight-pipeline/
├── src/
│   ├── config/                     # Settings
│   │   ├── settings.py             # All configuration, loaded from .env
│   │   └── prompts.py              # Claude extraction prompts (claim, search_keywords)
│   ├── db/                         # Shared DB Utilities
│   │   └── connection.py           # connect(), get_pool() context managers
│   ├── embed/                      # Embedding
│   │   ├── protocol.py             # Embedder Protocol interface
│   │   ├── local.py                # multilingual-e5-large 1024-dim (default)
│   │   ├── factory.py              # get_embedder() factory + vec_str()
│   │   └── backfill.py             # Batch fill NULL embeddings
│   ├── extract/                    # Insight Extraction
│   │   ├── batch_api.py            # Claude Batch API orchestration
│   │   ├── parse_results.py        # Batch result JSONL → DB
│   │   └── realtime.py             # Real-time insight extraction (expected_date)
│   ├── collect/                    # Data Collection
│   │   ├── mer_monitor.py          # Blog RSS watcher
│   │   ├── news_collector.py       # News headline RSS collection (15 feeds)
│   │   ├── feeds.py                # Google News RSS feed definitions
│   │   ├── keyword_extractor.py    # Keyword extraction (kiwipiepy + regex)
│   │   ├── posts.py                # JSON → mer_posts bulk loader
│   │   └── date_parser.py          # Korean date string parser
│   ├── verify/                     # Prediction Verification
│   │   ├── verifier.py             # Export verifiable predictions + Telegram notify
│   │   ├── headline_matcher.py     # Prediction ↔ headline keyword GIN matching
│   │   └── prompt.py               # Constants (batch size 20)
│   ├── search/                     # Hybrid Search
│   │   ├── bm25_index.py           # BM25 + kiwipiepy + pickle cache
│   │   ├── vector_index.py         # pgvector HNSW wrapper (1024-dim)
│   │   └── hybrid.py               # RRF fusion (α=0.6)
│   ├── eval/                       # Eval Pipeline
│   │   ├── experiment.py           # Ablation: vector vs BM25 vs hybrid
│   │   ├── eval_runner.py          # Main runner
│   │   ├── metrics.py              # Precision@K, Recall@K, MRR
│   │   ├── llm_judge.py            # LLM-as-judge (Claude Sonnet)
│   │   └── report.py               # Markdown + JSON report generator
│   ├── pipeline/                   # Orchestration
│   │   └── event_dispatcher.py     # APScheduler / Cloud Run Job
│   └── dashboard/                  # Streamlit Dashboard
│       ├── app.py                  # Layout & rendering
│       ├── queries.py              # DB query functions (psycopg2)
│       └── topics.py               # Topic classification (TOPIC_KEYWORDS)
├── scripts/
│   ├── run_job.py                  # Cloud Run Job entry point
│   ├── run_batch.py                # Batch extraction orchestrator
│   ├── naver_blog_scraper.py       # Blog scraper
│   ├── migrate_news_headlines.sql  # News DB migration
│   ├── ops/                        # Data operation scripts
│   │   ├── backfill_news.py        # News headline historical backfill
│   │   ├── import_manual_verdicts.py  # Manual verdict import
│   │   ├── verify_pipeline/        # 3-stage compressed verification pipeline
│   │   └── ...                     # Other operation scripts
│   └── eval/                       # Eval scripts
├── data/
│   ├── verify_pipeline/            # Verification pipeline intermediate results
│   ├── manual_verify/              # Manual verification results (by round)
│   └── audit/                      # Data quality audit results
├── eval_data/
│   └── gold_extended.json          # Gold dataset: 200 queries
├── docker-compose.yml
├── Dockerfile
├── requirements.txt                # Streamlit Cloud (lightweight)
└── requirements-full.txt           # Full dependencies
```

---

## License

MIT
