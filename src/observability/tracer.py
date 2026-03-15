"""
LLM 호출 추적 데코레이터 + context manager.

Claude API 호출을 래핑해 토큰 수, 비용, latency를 PostgreSQL에 기록한다.

사용법 (데코레이터):
    @trace_llm_call(name="agent_step", conn=conn, trace_id=trace_id)
    async def call_claude(messages, tools):
        return await client.messages.create(...)

사용법 (context manager):
    async with Tracer(conn=conn, trace_name="agent_run") as tracer:
        resp = await tracer.call(client.messages.create, model=..., messages=...)

토큰 가격 (claude-sonnet-4-6 기준, 2024):
    input:  $3.00 / 1M tokens
    output: $15.00 / 1M tokens
"""

import functools
import logging
import time
import uuid
from datetime import datetime

import asyncpg

from src.observability.storage import (
    insert_trace, finish_trace, insert_span,
)

log = logging.getLogger(__name__)

# 모델별 가격 (USD / 1M tokens)
_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":          (3.0,  15.0),
    "claude-haiku-4-5-20251001":  (0.25,  1.25),
}
_DEFAULT_PRICE = (3.0, 15.0)


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    in_price, out_price = _PRICES.get(model, _DEFAULT_PRICE)
    return (in_tok * in_price + out_tok * out_price) / 1_000_000


class Tracer:
    """
    에이전트 루프 전체를 하나의 trace로, 각 LLM 호출을 span으로 추적.

    async with Tracer(conn, trace_name="agent_run") as t:
        resp = await t.call(client.messages.create, model=..., ...)
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        trace_name: str,
        metadata: dict | None = None,
    ):
        self._conn = conn
        self._trace_name = trace_name
        self._metadata = metadata or {}
        self._trace_id = str(uuid.uuid4())
        self._start = datetime.now()
        self._total_in = 0
        self._total_out = 0
        self._total_cost = 0.0
        self._status = "ok"

    async def __aenter__(self):
        await insert_trace(
            self._conn, self._trace_id, self._trace_name,
            self._start, self._metadata,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._status = "error"
        await finish_trace(
            self._conn, self._trace_id, datetime.now(),
            self._total_in, self._total_out, self._total_cost, self._status,
        )

    async def call(self, api_fn, span_name: str = "llm_call", **kwargs):
        """
        API 함수를 호출하고 결과를 span으로 기록.

        api_fn: client.messages.create 등 async callable
        """
        span_id = str(uuid.uuid4())
        t0 = time.monotonic()
        error_msg = None
        resp = None

        try:
            resp = await api_fn(**kwargs)
        except Exception as e:
            error_msg = str(e)
            self._status = "error"
            raise
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            model = kwargs.get("model", "unknown")

            if resp is not None:
                in_tok  = getattr(getattr(resp, "usage", None), "input_tokens",  0) or 0
                out_tok = getattr(getattr(resp, "usage", None), "output_tokens", 0) or 0
            else:
                in_tok = out_tok = 0

            cost = _cost(model, in_tok, out_tok)
            self._total_in   += in_tok
            self._total_out  += out_tok
            self._total_cost += cost

            # tool_calls 추출
            tool_calls = []
            if resp is not None:
                for block in getattr(resp, "content", []):
                    if getattr(block, "type", None) == "tool_use":
                        tool_calls.append(block.name)

            await insert_span(
                conn=self._conn,
                span_id=span_id,
                trace_id=self._trace_id,
                name=span_name,
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                cost_usd=cost,
                tool_calls=tool_calls or None,
                error=error_msg,
            )
            log.debug(
                f"span={span_name} model={model} "
                f"in={in_tok} out={out_tok} cost=${cost:.5f} lat={latency_ms}ms"
            )

        return resp

    @property
    def trace_id(self) -> str:
        return self._trace_id


def trace_llm_call(
    name: str,
    conn: asyncpg.Connection,
    trace_id: str | None = None,
):
    """
    Claude API 호출 함수를 span으로 추적하는 데코레이터.

    trace_id가 None이면 호출마다 새 trace 생성.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            _trace_id = trace_id or str(uuid.uuid4())
            _start = datetime.now()
            span_id = str(uuid.uuid4())
            t0 = time.monotonic()
            error_msg = None
            resp = None

            if trace_id is None:
                await insert_trace(conn, _trace_id, name, _start)

            try:
                resp = await fn(*args, **kwargs)
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                latency_ms = int((time.monotonic() - t0) * 1000)
                # kwargs에서 model 추출 (fn의 kwargs 또는 args에서)
                model = kwargs.get("model", "unknown")
                in_tok  = getattr(getattr(resp, "usage", None), "input_tokens",  0) if resp else 0
                out_tok = getattr(getattr(resp, "usage", None), "output_tokens", 0) if resp else 0
                cost = _cost(model, in_tok, out_tok)

                await insert_span(
                    conn=conn, span_id=span_id, trace_id=_trace_id,
                    name=name, model=model,
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=latency_ms, cost_usd=cost,
                    error=error_msg,
                )
                if trace_id is None:
                    await finish_trace(conn, _trace_id, datetime.now(), in_tok, out_tok, cost,
                                       "error" if error_msg else "ok")
            return resp
        return wrapper
    return decorator
