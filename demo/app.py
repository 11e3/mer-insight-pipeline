"""
mer-insight-pipeline 공개 데모.

DB나 API 키 없이 동작 — sample_data.json만 사용.
Streamlit Community Cloud (share.streamlit.io)에 무료 배포 가능.

로컬 실행:
    streamlit run demo/app.py
"""

import json
from pathlib import Path

import streamlit as st
from rank_bm25 import BM25Okapi

# ── 데이터 로드 ───────────────────────────────────────────────────────────────

_DATA_PATH = Path(__file__).parent / "sample_data.json"


@st.cache_data
def load_data() -> dict:
    if not _DATA_PATH.exists():
        return {}
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


@st.cache_resource
def build_bm25(insights: list[dict]):
    """사전 토크나이징된 tokens 필드로 BM25 인덱스 빌드."""
    corpus = [ins["tokens"] for ins in insights]
    return BM25Okapi(corpus)


# ── 페이지 설정 ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="mer-insight-pipeline demo",
    page_icon="📊",
    layout="wide",
)

data = load_data()
insights  = data.get("insights",  [])
analyses  = data.get("analyses",  [])
pred_stats = data.get("prediction_stats", {})

bm25 = build_bm25(insights) if insights else None

# ── 사이드바 ──────────────────────────────────────────────────────────────────

page = st.sidebar.selectbox(
    "페이지",
    ["About", "인사이트 검색", "예측 정확도", "예측 대시보드", "최근 분석"],
)

if data.get("exported_at"):
    st.sidebar.caption(f"데이터 기준: {data['exported_at'][:10]}")
st.sidebar.caption(f"샘플 인사이트: {len(insights)}개 / 전체 {data.get('insight_count_total', 25090):,}개")

# ── About ─────────────────────────────────────────────────────────────────────

if page == "About":
    st.title("mer-insight-pipeline")
    st.markdown(
        "**mer-insight-pipeline**은 메르(ranto28) 한국 경제 블로그를 모니터링하고, "
        "Claude Batch API로 포스트에서 인사이트를 추출하며, "
        "새 글이 올라오면 수분 내에 인용 근거가 명시된 분석을 텔레그램 구독자에게 전송합니다."
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("처리된 포스트",  f"{data.get('post_count_total', 2193):,}개")
    col2.metric("추출된 인사이트", f"{data.get('insight_count_total', 25090):,}개")
    col3.metric("추적 예측",       f"{pred_stats.get('total', 5368):,}건")
    col4.metric("포스트당 비용",   "~$0.15")

    st.markdown("---")
    st.subheader("아키텍처 요약")
    st.markdown(
        """
| 레이어 | 기술 |
|--------|------|
| LLM (분석 · 에이전트) | Claude Sonnet 4.6 |
| LLM (추출 · 검증) | Claude Haiku 4.5 |
| 임베딩 | Vertex AI `text-multilingual-embedding-002` (768차원) |
| 벡터 DB | PostgreSQL 16 + pgvector (HNSW 인덱스) |
| 키워드 검색 | rank-bm25 + kiwipiepy 한국어 형태소 분석 |
| 하이브리드 융합 | Reciprocal Rank Fusion (RRF, α=0.4) |
| 환각 방지 | Citation guard — `[ref: ins_ID]` DB 존재 검증 |
| 배포 | GCP Cloud Run Jobs + Cloud SQL |
        """
    )

    st.markdown("---")
    st.markdown(
        "소스 코드: [github.com/11e3/mer-insight-pipeline](https://github.com/11e3/mer-insight-pipeline)"
    )

# ── 인사이트 검색 ─────────────────────────────────────────────────────────────

elif page == "인사이트 검색":
    st.title("인사이트 검색")
    st.caption("BM25 키워드 검색 (샘플 데이터 기준 — 실제 DB는 25,090개 인사이트)")

    query = st.text_input(
        "검색어",
        placeholder="예: 미국 금리 인하 전망, 반도체 사이클, 달러 강세",
    )

    k = st.slider("결과 수", min_value=3, max_value=10, value=5)

    if query and bm25:
        tokens = query.split()
        scores = bm25.get_scores(tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]

        st.markdown(f"**Top-{k} 결과** (쿼리: `{query}`)")
        for rank, idx in enumerate(top_indices, 1):
            ins = insights[idx]
            score = scores[idx]
            if score <= 0:
                break
            badge = {"rule": "📌 규칙", "evaluation": "📊 평가", "macro_view": "🌍 거시 관점"}.get(
                ins["type"], ins["type"]
            )
            with st.expander(f"#{rank}  {badge}  |  {ins['date']}  (score={score:.3f})", expanded=rank <= 2):
                st.write(ins["content"])
    elif query:
        st.warning("인사이트 데이터가 없습니다. demo/export_sample.py를 먼저 실행하세요.")

# ── 예측 정확도 ───────────────────────────────────────────────────────────────

elif page == "예측 정확도":
    st.title("예측 검증 정확도")
    st.markdown(
        "메르 블로그에서 추출된 모든 `prediction`-타입 인사이트는 `mer_predictions`에 저장되고, "
        "매일 Claude Haiku가 매크로 데이터·DART 공시·뉴스를 컨텍스트로 자동 검증합니다."
    )

    if pred_stats:
        acc = pred_stats.get("accuracy_pct", 0)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("검증 완료 정확도", f"{acc:.1f}%")
        col2.metric("CORRECT",  f"{pred_stats.get('correct', 0):,}건")
        col3.metric("INCORRECT", f"{pred_stats.get('incorrect', 0):,}건")
        col4.metric("PENDING",  f"{pred_stats.get('pending', 0):,}건",
                    help="조건 미충족 — 매일 재시도, 만기 없음")

        st.markdown("---")
        st.markdown(
            f"**전체 추적**: {pred_stats.get('total', 0):,}건  |  "
            f"**검증 완료**: {pred_stats.get('verified', 0):,}건"
        )
    else:
        st.info("통계 데이터 없음 — demo/export_sample.py를 실행해 sample_data.json을 생성하세요.")

    st.markdown("---")
    st.markdown(
        """
**판정 기준**

| 판정 | 조건 |
|------|------|
| `CORRECT` | 컨텍스트 또는 Claude 지식으로 예측 내용 확인됨 |
| `INCORRECT` | 근거에 의해 예측 내용이 반박됨 |
| `PENDING` | 조건 미충족 또는 정보 부족 — 다음날 재검증 |
        """
    )

# ── 예측 대시보드 (Full) ──────────────────────────────────────────────────────

elif page == "예측 대시보드":
    st.title("예측 검증 대시보드")
    st.markdown(
        "주제별 적중률, 월별 추이, 최근 검증 내역을 포함한 전체 예측 대시보드는 "
        "DB 연결이 필요합니다."
    )
    st.code("streamlit run src/dashboard/prediction_dashboard.py", language="bash")
    st.markdown(
        "**포함 항목:**\n"
        "1. 전체 요약 — 총 예측 수, CORRECT/INCORRECT/PENDING 비율 (파이 차트)\n"
        "2. 주제별 적중률 — 금리, 환율, 부동산, 주식, 원자재 분류별 accuracy\n"
        "3. 월별 추이 — 예측 수 + 적중률 변화 라인 차트\n"
        "4. 최근 검증 테이블 — 날짜, 예측 내용, 판정, 근거 요약"
    )

# ── 최근 분석 ─────────────────────────────────────────────────────────────────

elif page == "최근 분석":
    st.title("최근 자동 분석")
    st.caption("에이전트 루프(Claude Sonnet + 5 tools)가 새 포스트마다 생성하는 분석 (1,000자 미리보기)")

    if analyses:
        for a in analyses:
            with st.expander(f"📝 {a['date']}  —  {a['title'] or '(제목 없음)'}"):
                st.write(a["analysis_text"])
                if len(a["analysis_text"]) >= 1000:
                    st.caption("(1,000자 미리보기 — 실제 분석은 더 깁니다)")
    else:
        st.info("분석 데이터 없음 — demo/export_sample.py를 실행해 sample_data.json을 생성하세요.")
