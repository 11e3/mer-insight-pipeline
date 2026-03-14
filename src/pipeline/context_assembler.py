"""
Context Assembler: 새 이벤트 → 분석용 컨텍스트 조립 (RAG 핵심)
"""

import json
from datetime import date

import asyncpg
import anthropic

from config.settings import ANTHROPIC_API_KEY, MODEL_HAIKU
from config.prompts import ENTITY_EXTRACT_PROMPT
from src.extract.vertex_embedder import VertexEmbedder, vec_str
from src.pipeline.event_types import AnalysisConfig

client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


class ContextAssembler:

    def __init__(self, conn: asyncpg.Connection, embedder: VertexEmbedder):
        self.conn = conn
        self.embedder = embedder

    async def assemble(self, event: dict, config: AnalysisConfig) -> dict:
        context = {
            "event": event,
            "rules": [],
            "similar_analyses": [],
            "macro_context": {},
        }

        entities = await self._extract_entities(event)

        if config.max_rules > 0:
            context["rules"] = await self._retrieve_rules(
                event_text=event.get("content", ""),
                entities=entities,
                max_results=config.max_rules,
            )

        if config.include_macro:
            context["macro_context"] = await self._get_macro_context()

        context["similar_analyses"] = await self._find_similar_past(
            event_text=event.get("content", ""),
            limit=3,
        )

        return context

    # ─── Private ──────────────────────────────────────────────────────────────

    async def _extract_entities(self, event: dict) -> list[str]:
        """Haiku로 키워드/엔티티 추출."""
        try:
            resp = await client.messages.create(
                model=MODEL_HAIKU,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": ENTITY_EXTRACT_PROMPT.format(
                        title=event.get("title", ""),
                        content_snippet=(event.get("content", ""))[:500],
                    ),
                }],
            )
            text = resp.content[0].text.strip()
            # JSON 배열 추출
            start = text.find("[")
            end   = text.rfind("]") + 1
            if start != -1 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        return []

    async def _retrieve_rules(
        self,
        event_text: str,
        entities: list[str],
        max_results: int,
    ) -> list[dict]:
        """벡터 유사도 + 키워드 하이브리드 검색 + 시간 가중치 re-rank."""

        # 1. 벡터 검색
        vec = vec_str(await self.embedder.embed_query(event_text[:500]))

        vector_rows = await self.conn.fetch("""
            SELECT mi.id, mi.content, mi.structured_data, mi.insight_type,
                   mp.date,
                   1 - (mi.embedding <=> $1::vector) AS similarity
            FROM mer_insights mi
            JOIN mer_posts mp ON mi.post_id = mp.id
            WHERE mi.embedding IS NOT NULL
              AND mi.insight_type IN ('rule', 'evaluation', 'macro_view')
              AND (mi.is_canonical IS NULL OR mi.is_canonical = TRUE)
            ORDER BY mi.embedding <=> $1::vector
            LIMIT $2
        """, vec, max_results * 3)

        # 2. 키워드 매칭
        keyword_rows = []
        if entities:
            patterns = [f"%{e}%" for e in entities[:10]]
            keyword_rows = await self.conn.fetch("""
                SELECT mi.id, mi.content, mi.structured_data, mi.insight_type,
                       mp.date, 0.8 AS similarity
                FROM mer_insights mi
                JOIN mer_posts mp ON mi.post_id = mp.id
                WHERE mi.insight_type IN ('rule', 'evaluation', 'macro_view')
                  AND (mi.is_canonical IS NULL OR mi.is_canonical = TRUE)
                  AND (
                      mi.structured_data->>'applicable_domain' ILIKE ANY($1::text[])
                      OR mi.structured_data->>'sector'          ILIKE ANY($1::text[])
                      OR mi.structured_data->>'entity_name'     ILIKE ANY($1::text[])
                      OR mi.structured_data->>'macro_theme'     ILIKE ANY($1::text[])
                  )
                LIMIT $2
            """, patterns, max_results * 2)

        # 3. Re-rank
        keyword_ids = {r["id"] for r in keyword_rows}
        all_results: dict[int, dict] = {}

        for r in list(vector_rows) + list(keyword_rows):
            rid = r["id"]
            if rid not in all_results:
                all_results[rid] = dict(r)
                all_results[rid]["score"] = 0.0

            vector_score  = float(r.get("similarity") or 0) * 0.5
            keyword_bonus = 0.3 if rid in keyword_ids else 0.0
            days_ago      = (date.today() - r["date"]).days if r["date"] else 730
            recency_score = max(0.0, 1 - days_ago / 730) * 0.2

            score = vector_score + keyword_bonus + recency_score
            if score > all_results[rid]["score"]:
                all_results[rid]["score"] = score

        ranked = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:max_results]

    async def _get_macro_context(self) -> dict:
        row = await self.conn.fetchrow("""
            SELECT * FROM macro_daily
            WHERE date <= CURRENT_DATE
            ORDER BY date DESC LIMIT 1
        """)
        return dict(row) if row else {}

    async def _find_similar_past(self, event_text: str, limit: int) -> list[dict]:
        """과거 유사 이벤트에 대한 분석 결과 검색."""
        vec = vec_str(await self.embedder.embed_query(event_text[:500]))

        rows = await self.conn.fetch("""
            SELECT e.title, e.event_type, e.event_date,
                   aa.analysis_text,
                   1 - (e.embedding <=> $1::vector) AS similarity
            FROM events e
            JOIN auto_analyses aa ON aa.event_id = e.id
            WHERE e.embedding IS NOT NULL
            ORDER BY e.embedding <=> $1::vector
            LIMIT $2
        """, vec, limit)

        return [dict(r) for r in rows]
