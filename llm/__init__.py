"""LLM façade: structured-output runner, parallelism, cost and usage helpers.

Domain code should call :func:`llm.json_runner.execute_llm_task` with assembled
messages, a Pydantic ``response_model``, optional ``postprocess``, and optional
``fallback`` / ``fallback_factory``. :func:`llm.json_runner.run_json_llm` remains
for tests and callers that still use the :class:`llm.json_runner.JsonLlmSpec`
protocol. Parallel fan-out uses :func:`llm.json_runner.run_parallel_map`.
"""

from grading_models import usage_cost_usd
from llm.json_runner import (
    MAX_JSON_LLM_ATTEMPTS,
    MAX_VALIDATION_RETRIES,
    JsonLlmApiError,
    JsonLlmError,
    JsonLlmExhaustedError,
    JsonLlmParseError,
    JsonLlmValidationError,
    execute_llm_task,
    run_json_llm,
    run_parallel_map,
)
from results_models import (
    GRADED_RESULT_USAGE_KEY,
    TokenUsage,
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)
