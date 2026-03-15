"""
에이전트 tool 함수 구현 + Claude tool_use 스펙 정의.

각 함수는 tool_use 결과를 JSON 직렬화 가능한 dict/list로 반환한다.
"""

import json
import logging

import asyncpg

from src.extract.vertex_embedder import Embedder
from src.search.bm25_index import BM25Index
from src.search.hybrid import HybridSearcher

log = logging.getLogger(__name__)

# ─── Tool 스펙 (Claude API tool_use 형식) ─────────────────────────────────────

TOOL_SPECS = [
    {
        "name": "search_past_insights",
        "description": (
            "기존 인사이트 DB에서 쿼리와 관련된 인사이트를 검색해요. "
            "벡터 유사도 + BM25 하이브리드 방식으로 검색해요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색 쿼리 (한국어)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "반환할 결과 수 (기본: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_contradiction",
        "description": (
            "새 포스트의 주장과 과거 인사이트를 비교해 모순 여부를 판단해요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_claim": {
                    "type": "string",
                    "description": "새 포스트에서 확인하고 싶은 주장",
                },
                "past_insight_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "비교할 과거 인사이트 ID 목록",
                },
            },
            "required": ["new_claim", "past_insight_ids"],
        },
    },
    {
        "name": "classify_novelty",
        "description": (
            "새 인사이트가 기존 클러스터에 속하는지, 완전히 새로운 토픽인지 판단해요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "insight_text": {
                    "type": "string",
                    "description": "분류할 인사이트 텍스트",
                },
            },
            "required": ["insight_text"],
        },
    },
    {
        "name": "get_topic_history",
        "description": (
            "특정 주제에 대한 메르의 과거 발언을 시간순으로 조회해요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "조회할 주제 (예: 금리, 부동산, 반도체)",
                },
                "limit": {
                    "type": "integer",
                    "description": "반환할 최대 개수 (기본: 10)",
                    "default": 10,
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "generate_comparative_analysis",
        "description": (
            "과거 인사이트와 새 포스트를 비교 분석해요. "
            "모순점, 발전된 논점, 새로운 시각을 정리해요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_post_summary": {
                    "type": "string",
                    "description": "새 포스트의 핵심 내용 요약",
                },
                "related_insight_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "비교할 과거 인사이트 ID 목록",
                },
            },
            "required": ["new_post_summary", "related_insight_ids"],
        },
    },
]


# ─── Tool 실행기 ─────────────────────────────────────────────────────────────

class ToolExecutor:
    """에이전트 tool 호출을 실제 DB/검색으로 연결."""

    def __init__(
        self,
        conn: asyncpg.Connection,
        embedder: Embedder,
        bm25_index: BM25Index,
    ):
        self._conn = conn
        self._searcher = HybridSearcher(conn, embedder, bm25_index, alpha=0.4)

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """tool 이름과 입력을 받아 결과를 JSON 문자열로 반환."""
        try:
            if tool_name == "search_past_insights":
                result = await self._search_past_insights(**tool_input)
            elif tool_name == "check_contradiction":
                result = await self._check_contradiction(**tool_input)
            elif tool_name == "classify_novelty":
                result = await self._classify_novelty(**tool_input)
            elif tool_name == "get_topic_history":
                result = await self._get_topic_history(**tool_input)
            elif tool_name == "generate_comparative_analysis":
                result = await self._generate_comparative_analysis(**tool_input)
            else:
                result = {"error": f"알 수 없는 tool: {tool_name}"}
        except Exception as e:
            log.error(f"Tool 실행 오류 ({tool_name}): {e}")
            result = {"error": str(e)}

        return json.dumps(result, ensure_ascii=False, default=str)

    # ─── 개별 tool 구현 ────────────────────────────────────────────────────

    async def _search_past_insights(
        self, query: str, top_k: int = 5
    ) -> list[dict]:
        results = await self._searcher.search(query, top_k=top_k)
        return [
            {
                "id": r["id"],
                "content": (r.get("content") or "")[:400],
                "insight_type": r.get("insight_type"),
                "date": str(r.get("date", "")),
                "score": round(r.get("score", 0), 4),
            }
            for r in results
        ]

    async def _check_contradiction(
        self, new_claim: str, past_insight_ids: list[int]
    ) -> dict:
        if not past_insight_ids:
            return {"is_contradicted": False, "contradicting_insights": [], "explanation": "비교할 과거 인사이트 없음"}

        rows = await self._conn.fetch("""
            SELECT id, content FROM mer_insights
            WHERE id = ANY($1::int[])
        """, past_insight_ids)

        contradicting = []
        for row in rows:
            content = row["content"]
            # 단순 키워드 기반 모순 탐지 (LLM judge는 guard 모듈에서 담당)
            # 여기서는 의미적으로 반대 방향 지표 키워드를 사용
            new_lower = new_claim.lower()
            past_lower = content.lower()
            opposite_pairs = [
                ("상승", "하락"), ("긍정", "부정"), ("인하", "인상"),
                ("확대", "축소"), ("낙관", "비관"),
            ]
            for pos, neg in opposite_pairs:
                if (pos in new_lower and neg in past_lower) or (neg in new_lower and pos in past_lower):
                    contradicting.append({
                        "id": row["id"],
                        "content": content[:300],
                        "conflict_keywords": [pos, neg],
                    })
                    break

        return {
            "is_contradicted": len(contradicting) > 0,
            "contradicting_insights": contradicting,
            "explanation": (
                f"{len(contradicting)}개 인사이트와 잠재적 모순 발견"
                if contradicting else "모순 없음"
            ),
        }

    async def _classify_novelty(self, insight_text: str) -> dict:
        """클러스터 DB 기반 신규성 분류."""
        # cluster_id가 있는 가장 유사한 인사이트를 벡터 검색으로 찾음
        results = await self._searcher.search(insight_text, top_k=3, fetch_content=True)

        if not results:
            return {"is_novel": True, "nearest_cluster": None, "distance": 1.0}

        best = results[0]
        best_score = best.get("score", 0)
        # RRF 점수 0.015 이상 ≈ 유사도 높음 (실험적 threshold)
        is_novel = best_score < 0.012

        # cluster_id 조회
        row = await self._conn.fetchrow(
            "SELECT cluster_id FROM mer_insights WHERE id = $1", best["id"]
        )
        cluster_id = row["cluster_id"] if row else None

        return {
            "is_novel": is_novel,
            "nearest_cluster": cluster_id,
            "nearest_insight_id": best["id"],
            "nearest_content": (best.get("content") or "")[:200],
            "similarity_score": round(best_score, 4),
        }

    async def _get_topic_history(self, topic: str, limit: int = 10) -> list[dict]:
        rows = await self._conn.fetch("""
            SELECT mi.id, mi.content, mi.insight_type, mp.date
            FROM mer_insights mi
            JOIN mer_posts mp ON mi.post_id = mp.id
            WHERE mi.content ILIKE $1
              AND (mi.is_canonical IS NULL OR mi.is_canonical = TRUE)
            ORDER BY mp.date ASC
            LIMIT $2
        """, f"%{topic}%", limit)

        return [
            {
                "id": r["id"],
                "content": r["content"][:300],
                "insight_type": r["insight_type"],
                "date": str(r["date"]),
            }
            for r in rows
        ]

    async def _generate_comparative_analysis(
        self, new_post_summary: str, related_insight_ids: list[int]
    ) -> dict:
        if not related_insight_ids:
            return {"analysis": "비교할 과거 인사이트가 없어요.", "developments": [], "contradictions": []}

        rows = await self._conn.fetch("""
            SELECT id, content, insight_type FROM mer_insights
            WHERE id = ANY($1::int[])
        """, related_insight_ids)

        past_texts = [f"[{r['insight_type']}] {r['content'][:200]}" for r in rows]
        past_block = "\n".join(past_texts)

        # 간단 비교 분석 (상세 비교는 LLM agent가 담당)
        return {
            "new_post_summary": new_post_summary,
            "past_insights_count": len(rows),
            "past_insights_sample": past_block[:800],
            "note": "이 데이터를 바탕으로 비교 분석을 생성하세요.",
        }
