"""LLM façade: JSON chat completions, retries, parallelism, cost helpers."""

from llm.chat_completion import complete_json_chat
from llm.cost import usage_cost_usd
from llm.parallel import iter_unordered_parallel_results
from llm.retry import retry_with_exponential_backoff
from llm.types import JsonCompletionResult, TokenUsage

__all__ = [
    "JsonCompletionResult",
    "TokenUsage",
    "complete_json_chat",
    "iter_unordered_parallel_results",
    "retry_with_exponential_backoff",
    "usage_cost_usd",
]
