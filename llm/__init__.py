"""LLM façade: JSON chat completions, retries, parallelism, cost helpers."""

from llm.chat_completion import complete_json_chat
from llm.cost import usage_cost_usd
from llm.parallel import iter_unordered_parallel_results
from llm.retry import retry_with_exponential_backoff
from llm.types import JsonCompletionResult, TokenUsage
from llm.usage_helpers import (
    GRADED_RESULT_USAGE_KEY,
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)

__all__ = [
    "GRADED_RESULT_USAGE_KEY",
    "JsonCompletionResult",
    "TokenUsage",
    "complete_json_chat",
    "detach_usage_from_graded_result",
    "graded_usage_summary_event",
    "iter_unordered_parallel_results",
    "merge_graded_usage",
    "retry_with_exponential_backoff",
    "usage_cost_usd",
]
