"""
BM25 인덱스: 한국어 형태소 분석(kiwipiepy) + rank_bm25

Usage:
    index = BM25Index()
    await index.build(conn)           # DB에서 인사이트 로드 후 빌드
    index.save("data/bm25.pkl")       # 직렬화
    index.load("data/bm25.pkl")       # 역직렬화
    results = index.search("금리 인하", top_k=10)
    # → [(insight_id, score), ...]
"""

import pickle
import logging
from pathlib import Path

import asyncpg
from rank_bm25 import BM25Okapi

log = logging.getLogger(__name__)

# kiwipiepy 선택적 import (설치 안 됐으면 공백 분리로 fallback)
try:
    from kiwipiepy import Kiwi
    _kiwi = Kiwi()

    def _tokenize(text: str) -> list[str]:
        tokens = _kiwi.tokenize(text)
        return [t.form for t in tokens if len(t.form) > 1]

except ImportError:
    log.warning("kiwipiepy 미설치 — 공백 분리로 fallback")

    def _tokenize(text: str) -> list[str]:
        return [t for t in text.split() if len(t) > 1]


class BM25Index:
    """25,090건 인사이트 텍스트 BM25 인덱스."""

    def __init__(self):
        self._bm25: BM25Okapi | None = None
        self._doc_ids: list[int] = []       # corpus 순서 ↔ insights.id 매핑

    # ─── 빌드 ──────────────────────────────────────────────────────────────

    async def build(self, conn: asyncpg.Connection) -> None:
        """DB에서 모든 인사이트를 로드해 BM25 인덱스 빌드."""
        log.info("BM25 인덱스 빌드 시작...")
        rows = await conn.fetch("""
            SELECT id, content
            FROM insights
            WHERE insight_type IN ('rule', 'evaluation', 'macro_view')
              AND (is_canonical IS NULL OR is_canonical = TRUE)
            ORDER BY id
        """)

        self._doc_ids = [r["id"] for r in rows]
        corpus = [_tokenize(r["content"]) for r in rows]
        if not corpus:
            log.warning("BM25 인덱스: 인사이트 데이터 없음 — 빈 인덱스로 초기화")
            self._bm25 = None
        else:
            self._bm25 = BM25Okapi(corpus)
        log.info(f"BM25 인덱스 완료: {len(self._doc_ids)}개 문서")

    # ─── 저장/로드 ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"bm25": self._bm25, "doc_ids": self._doc_ids}, f)
        log.info(f"BM25 인덱스 저장: {path}")

    def load(self, path: str | Path) -> bool:
        """로드 성공 여부 반환."""
        p = Path(path)
        if not p.exists():
            return False
        with open(p, "rb") as f:
            data = pickle.load(f)
        self._bm25 = data["bm25"]
        self._doc_ids = data["doc_ids"]
        log.info(f"BM25 인덱스 로드: {len(self._doc_ids)}개 문서")
        return True

    # ─── 검색 ──────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """
        BM25 검색.

        Returns:
            [(insight_id, bm25_score), ...] top_k 개, 점수 내림차순
        """
        if self._bm25 is None:
            return []

        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)

        # top_k 인덱스 추출 (numpy argsort 대신 직접)
        indexed = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
        return [(self._doc_ids[i], float(s)) for i, s in indexed if s > 0]

    @property
    def is_ready(self) -> bool:
        return self._bm25 is not None
