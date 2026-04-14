"""LLM façade: structured-output runner and parallelism.

Domain code calls :func:`llm.json_runner.execute_llm_task` with assembled
messages, a Pydantic ``response_model``, optional ``postprocess``, and optional
``fallback`` / ``fallback_factory``. Parallel fan-out uses
:func:`llm.json_runner.run_jobs`.
"""
