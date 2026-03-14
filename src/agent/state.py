"""에이전트 루프 상태 관리."""

from dataclasses import dataclass, field


@dataclass
class AgentState:
    """단일 에이전트 실행 세션 상태."""

    messages: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)  # 누적 tool 결과
    iteration: int = 0
    final_analysis: str = ""

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: list[dict]) -> None:
        """content: anthropic API의 content block 리스트."""
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict]) -> None:
        """tool_result content block 리스트를 user 메시지로 추가."""
        self.messages.append({"role": "user", "content": results})
        self.tool_results.extend(results)

    def increment(self) -> None:
        self.iteration += 1

    @property
    def is_done(self) -> bool:
        return bool(self.final_analysis)
