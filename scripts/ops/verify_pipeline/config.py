"""3단계 검증 파이프라인 설정 + 비용 추적."""

import json
from datetime import datetime
from pathlib import Path

MODEL = "claude-haiku-4-5-20251001"
BUDGET_LIMIT = 15.0  # dollars, 안전 마진 포함
OUTPUT_DIR = Path("data/verify_pipeline")

# Haiku pricing (per 1M tokens)
INPUT_COST_PER_M = 0.80
OUTPUT_COST_PER_M = 4.00


class CostTracker:
    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.log_path = OUTPUT_DIR / "cost_log.jsonl"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def add(self, stage: str, input_tokens: int, output_tokens: int,
            extra_cost: float = 0.0):
        cost = (
            input_tokens * INPUT_COST_PER_M
            + output_tokens * OUTPUT_COST_PER_M
        ) / 1_000_000 + extra_cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        with open(self.log_path, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "stage": stage,
                "input": input_tokens,
                "output": output_tokens,
                "extra_cost": round(extra_cost, 4),
                "cost": round(cost, 4),
                "cumulative": round(self.total_cost, 4),
            }) + "\n")
        return cost

    def check_budget(self):
        if self.total_cost > BUDGET_LIMIT:
            raise RuntimeError(
                f"예산 초과! 누적 ${self.total_cost:.2f} > 한도 ${BUDGET_LIMIT}"
            )

    def summary(self):
        return (
            f"누적 비용: ${self.total_cost:.2f} / ${BUDGET_LIMIT}\n"
            f"토큰: input={self.total_input_tokens:,} output={self.total_output_tokens:,}"
        )
