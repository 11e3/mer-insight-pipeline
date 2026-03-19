"""Stage 3: 검색 + 판정 — Haiku + web_search. 비용 대부분 여기서 발생."""

import asyncio
import json
import os
import re

import anthropic
from dotenv import load_dotenv

from .config import MODEL, OUTPUT_DIR, CostTracker

load_dotenv()

SYSTEM = """\
아래 검증 포인트에 대해 웹 검색으로 사실을 확인하고 판정하라.

출력: JSON만.
{
  "cp_id": N,
  "fact_found": "확인된 사실 요약 (1-2문장)",
  "verdict": "CONFIRMED|DENIED|UNKNOWN",
  "source_url": "근거 URL"
}

- CONFIRMED: 검증 포인트의 내용이 사실로 확인됨
- DENIED: 검증 포인트의 내용이 사실과 반대
- UNKNOWN: 검색으로 확인 불가
"""

COST_WARN_THRESHOLD = 13.0  # 이 금액 초과 시 중단


def _parse_result(content_blocks: list) -> dict | None:
    texts = []
    for block in content_blocks:
        if hasattr(block, "text"):
            texts.append(block.text)
    raw = " ".join(texts)
    for m in re.finditer(r"\{[^{}]+\}", raw):
        try:
            obj = json.loads(m.group())
            if "verdict" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None


async def run(tracker: CostTracker, dry_run: bool = False) -> list[dict]:
    """검증 포인트별 web_search + 판정."""
    cp_path = OUTPUT_DIR / "stage2_checkpoints.json"
    if not cp_path.exists():
        if dry_run:
            print("  stage2 결과 없음 - 300개 검증 포인트 기준 추정")
            est = 300 * 0.035
            print(f"  예상 비용: ${est:.2f}")
            return []
        raise FileNotFoundError(f"{cp_path} 없음. stage2를 먼저 실행하세요.")
    checkpoints = json.loads(cp_path.read_text(encoding="utf-8"))

    # 이미 처리된 결과 로드 (재개 지원)
    results_path = OUTPUT_DIR / "stage3_results.json"
    existing_results = []
    done_ids = set()
    if results_path.exists():
        existing_results = json.loads(results_path.read_text(encoding="utf-8"))
        done_ids = {r["cp_id"] for r in existing_results}

    remaining = [cp for cp in checkpoints if cp["cp_id"] not in done_ids]
    print(f"[Stage 3] 검색+검증: {len(checkpoints)}개 중 {len(remaining)}개 남음")

    if dry_run:
        est = len(remaining) * 0.035  # 건당 ~$0.035
        print(f"  예상 비용: ${est:.2f}")
        return existing_results

    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results = list(existing_results)
    incomplete = False

    for i, cp in enumerate(remaining):
        # 비용 체크
        if tracker.total_cost > COST_WARN_THRESHOLD:
            print(f"  ⚠️ 비용 ${tracker.total_cost:.2f} > ${COST_WARN_THRESHOLD} — 중단")
            incomplete = True
            break

        user_msg = (
            f'검증 포인트 #{cp["cp_id"]}: {cp["query"]}\n'
            f'예상 정답 (CORRECT 기준): {cp.get("expected_answer_for_correct", "N/A")}'
        )

        try:
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=[{"type": "text", "text": SYSTEM}],
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": user_msg}],
            )

            inp = resp.usage.input_tokens
            out = resp.usage.output_tokens
            tracker.add("stage3", inp, out)

            result = _parse_result(resp.content)
            if result:
                result["cp_id"] = cp["cp_id"]
                result["prediction_ids"] = cp["prediction_ids"]
                result["expected_answer_for_correct"] = cp.get("expected_answer_for_correct", "")
                results.append(result)
            else:
                results.append({
                    "cp_id": cp["cp_id"],
                    "prediction_ids": cp["prediction_ids"],
                    "verdict": "UNKNOWN",
                    "fact_found": "파싱 실패",
                    "source_url": "",
                })

        except Exception as e:
            print(f"  ⚠️ CP #{cp['cp_id']} 오류: {str(e)[:60]}")
            results.append({
                "cp_id": cp["cp_id"],
                "prediction_ids": cp["prediction_ids"],
                "verdict": "UNKNOWN",
                "fact_found": f"API 오류: {str(e)[:40]}",
                "source_url": "",
            })

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(remaining)} 완료 — {tracker.summary()}")
            # 중간 저장
            results_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        await asyncio.sleep(0.5)

    # 최종 저장
    save_data = results
    if incomplete:
        save_data = {"incomplete": True, "results": results}

    results_path.write_text(
        json.dumps(save_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    verdicts = {"CONFIRMED": 0, "DENIED": 0, "UNKNOWN": 0}
    for r in results:
        verdicts[r.get("verdict", "UNKNOWN")] = verdicts.get(r.get("verdict", "UNKNOWN"), 0) + 1

    print(f"[Stage 3] 완료: {len(results)}건")
    print(f"  CONFIRMED={verdicts['CONFIRMED']} DENIED={verdicts['DENIED']} UNKNOWN={verdicts['UNKNOWN']}")
    if incomplete:
        print(f"  ⚠️ 예산 제한으로 {len(remaining) - len(results) + len(existing_results)}건 미처리")
    print(tracker.summary())

    return results
