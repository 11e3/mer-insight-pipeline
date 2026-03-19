"""검증 데이터 오염률 감사 — 결과 비교 + 통계.

사용법:
    1. data/audit/sample_50.json의 audit_verdict, audit_reason 필드를 채운다
    2. python scripts/ops/import_audit_results.py
"""

import json
import math
import sys
from pathlib import Path

SAMPLE_PATH = Path("data/audit/sample_50.json")


def wilson_ci(p_hat: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    denom = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * n)) / n)
    lo = (centre - spread) / denom
    hi = (centre + spread) / denom
    return max(0.0, lo), min(1.0, hi)


def main() -> None:
    if not SAMPLE_PATH.exists():
        print(f"파일 없음: {SAMPLE_PATH}")
        print("먼저 python scripts/ops/export_audit_sample.py 를 실행하세요.")
        sys.exit(1)

    samples = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))

    filled = [s for s in samples if s.get("audit_verdict")]
    if not filled:
        print("audit_verdict가 채워진 건이 없습니다.")
        print("sample_50.json의 audit_verdict 필드를 채운 후 다시 실행하세요.")
        sys.exit(1)

    n = len(filled)
    matches = 0
    mismatches = 0
    flips = 0  # CORRECT↔INCORRECT
    to_pending = 0

    for s in filled:
        orig = s["original_verdict"]
        audit = s["audit_verdict"].upper().strip()
        s["audit_verdict"] = audit

        if orig == audit:
            s["match"] = True
            matches += 1
        else:
            s["match"] = False
            mismatches += 1
            if audit == "PENDING":
                to_pending += 1
            elif {orig, audit} == {"CORRECT", "INCORRECT"}:
                flips += 1

    # 결과를 다시 저장
    SAMPLE_PATH.write_text(
        json.dumps(samples, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 통계 출력
    mismatch_rate = mismatches / n
    lo, hi = wilson_ci(mismatch_rate, n)

    print("=== 오염률 감사 결과 ===")
    print(f"샘플 크기: {n}")
    print(f"일치: {matches} ({matches / n * 100:.1f}%)")
    print(f"불일치: {mismatches} ({mismatch_rate * 100:.1f}%)")
    print(f"  - 판정 상반 (CORRECT↔INCORRECT): {flips}")
    print(f"  - PENDING 전환: {to_pending}")
    print(f"95% CI: [{lo * 100:.1f}%, {hi * 100:.1f}%]")

    if n < len(samples):
        print(f"\n참고: 전체 {len(samples)}건 중 {n}건만 audit_verdict가 채워져 있습니다.")


if __name__ == "__main__":
    main()
