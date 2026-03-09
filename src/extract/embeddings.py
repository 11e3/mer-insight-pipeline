"""
mer_insights 테이블의 embedding 컬럼 채우기.
multilingual-e5-large 모델 사용 (로컬, 무료).

Usage:
    python -m src.extract.embeddings
"""

import asyncio
import asyncpg
import torch
from tqdm import tqdm
from sentence_transformers import SentenceTransformer

from config.settings import DATABASE_URL, EMBEDDING_MODEL, EMBEDDING_BATCH_SIZE


def get_embedder() -> SentenceTransformer:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"임베딩 모델 로딩: {EMBEDDING_MODEL} (device={device})")
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    return model


def embed_texts(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    """
    multilingual-e5-large는 'query: ' / 'passage: ' prefix를 붙여야 성능이 좋음.
    인사이트는 'passage:'로 처리.
    """
    prefixed = [f"passage: {t}" for t in texts]
    vecs = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return vecs.tolist()


async def fill_embeddings():
    conn = await asyncpg.connect(DATABASE_URL)

    # embedding이 비어있는 인사이트 조회
    rows = await conn.fetch("""
        SELECT id, content FROM mer_insights
        WHERE embedding IS NULL
        ORDER BY id
    """)

    if not rows:
        print("모든 인사이트에 embedding이 존재합니다.")
        await conn.close()
        return

    print(f"embedding 생성 대상: {len(rows)}개\n")
    model = get_embedder()

    batch_ids, batch_texts = [], []
    total = 0

    async def flush():
        nonlocal total
        if not batch_ids:
            return
        vecs = embed_texts(model, batch_texts)
        for rid, vec in zip(batch_ids, vecs):
            # asyncpg는 pgvector 타입을 모르므로 문자열로 변환 후 캐스팅
            vec_str = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
            await conn.execute(
                "UPDATE mer_insights SET embedding = $1::vector WHERE id = $2",
                vec_str, rid
            )
        total += len(batch_ids)
        batch_ids.clear()
        batch_texts.clear()

    for row in tqdm(rows, desc="embedding 생성"):
        batch_ids.append(row["id"])
        batch_texts.append(row["content"])
        if len(batch_ids) >= EMBEDDING_BATCH_SIZE:
            await flush()

    await flush()
    await conn.close()
    print(f"\n완료: {total}개 embedding 저장")


if __name__ == "__main__":
    asyncio.run(fill_embeddings())
