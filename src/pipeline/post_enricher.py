"""
PostEnricher — 메르 새 글 알림에 부가 컨텍스트를 추가한다.

1. 관련 과거 메르 글
2. 메르의 과거 포지션 (유사 주제)
3. 행동 시그널 추출
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import anthropic
import asyncpg

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU
from src.pipeline.types import EnrichResult, Post
from src.extract.vertex_embedder import VertexEmbedder

log = logging.getLogger(__name__)


class PostEnricher:
    def __init__(self, conn: asyncpg.Connection, embedder: VertexEmbedder):
        self.conn = conn
        self.embedder = embedder
        self._claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def enrich(self, post: Post, analysis: str) -> EnrichResult:
        """3개 부가정보를 비동기로 수집해 반환. 임베딩은 한 번만 계산."""
        text = f"{post['title']} {post.get('content_text', '')[:300]}"
        vecs = await self.embedder.embed_passages([text])
        query_vec = vecs[0]

        results = await asyncio.gather(
            self._related_posts(query_vec, post["url"]),
            self._past_position(query_vec, post["url"], post["title"]),
            self._action_signal(analysis),
            return_exceptions=True,
        )
        keys: list[str] = ["related_posts", "past_position", "action_signal"]
        return {k: (v if not isinstance(v, Exception) else None) for k, v in zip(keys, results)}  # type: ignore[return-value]

    # ── 1. 관련 과거 메르 글 ────────────────────────────────────────────────

    async def _related_posts(self, query_vec: list[float], exclude_url: str) -> list[dict]:
        """벡터 검색으로 유사한 과거 메르 글 2~3개 반환."""
        vec_str = f"[{','.join(str(x) for x in query_vec)}]"

        rows = await self.conn.fetch("""
            SELECT e.id, e.title, e.event_date, e.source,
                   1 - (e.embedding <=> $1::vector) AS similarity
            FROM events e
            WHERE e.event_type = 'mer_new_post'
              AND e.source != $2
              AND e.embedding IS NOT NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT 5
        """, vec_str, exclude_url)

        return [
            {
                "title": r["title"],
                "date": r["event_date"].strftime("%Y-%m-%d") if r["event_date"] else "",
                "url": r["source"] or "",
                "similarity": round(r["similarity"] * 100),
            }
            for r in rows
            if r["similarity"] > 0.6
        ][:3]

    # ── 2. 메르의 과거 포지션 ──────────────────────────────────────────────

    async def _past_position(self, query_vec: list[float], exclude_url: str, title: str) -> Optional[str]:
        """유사 주제 과거 분석에서 메르의 핵심 시각을 1~2문장으로 요약."""
        vec_str = f"[{','.join(str(x) for x in query_vec)}]"

        rows = await self.conn.fetch("""
            SELECT aa.analysis_text, e.event_date
            FROM auto_analyses aa
            JOIN events e ON e.id = aa.event_id
            WHERE e.event_type = 'mer_new_post'
              AND e.source != $2
              AND e.embedding IS NOT NULL
              AND aa.analysis_text IS NOT NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT 3
        """, vec_str, exclude_url)

        if not rows:
            return None

        combined = "\n\n---\n\n".join(
            f"[{r['event_date'].strftime('%Y-%m-%d')}]\n{r['analysis_text'][:600]}"
            for r in rows
        )

        resp = await self._claude.messages.create(
            model=MODEL_HAIKU,
            max_tokens=200,
            system="당신은 메르(경제 블로거)의 과거 글들을 읽고 해당 주제에 대한 핵심 시각을 1~2문장으로 요약하는 역할입니다. 간결하게, 한국어로만 답하세요.",
            messages=[{
                "role": "user",
                "content": f"현재 글 주제: {title}\n\n과거 유사 분석들:\n{combined}\n\n→ 이 주제에 대한 메르의 핵심 시각을 1~2문장으로 요약해주세요.",
            }],
        )
        return resp.content[0].text.strip()

    # ── 3. 행동 시그널 ────────────────────────────────────────────────────

    async def _action_signal(self, analysis: str) -> Optional[str]:
        """분석 텍스트에서 행동 시그널(매수/매도/관망/주시 등)을 추출."""
        resp = await self._claude.messages.create(
            model=MODEL_HAIKU,
            max_tokens=80,
            system="투자 분석 텍스트에서 행동 시그널을 추출합니다. '관망', '매수 검토', '리스크 주시', '단기 수혜' 같은 키워드 3~5개를 콤마로 구분해 한 줄로만 답하세요. 분석이 없으면 '시그널 없음'.",
            messages=[{"role": "user", "content": analysis[:2000]}],
        )
        signal = resp.content[0].text.strip()
        return signal if signal != "시그널 없음" else None
