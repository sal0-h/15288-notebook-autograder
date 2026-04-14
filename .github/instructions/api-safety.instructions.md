---
description: "Use when editing app.py or writing API endpoints. Covers partial payload updates, untrusted input boundaries, thread-safety for grading and results locks, and assignment-scoped logging."
name: "API Safety and Concurrency"
applyTo: "{**/app.py,**/prompt_builder.py}"
---

# API Safety and Concurrency Guidelines

## Partial Payload Validation

UI sends partial updates to `/config`. Do NOT assume all fields are present:
- Validate only the fields provided in the request.
- Merge partial updates into the existing config without overwriting unspecified fields.
- Never treat a missing field as null or reset to default; preserve the existing value.

**Example**: If the request has `rubric_model` but no `workers`, keep the stored `workers` value unchanged.

## Untrusted Input Boundaries

Student notebook content in prompts is untrusted input. Maintain injection protection:
- Keep `<<<STUDENT_SUBMISSION>>>` boundaries in prompts to separate student content from instructions.
- Use sanitizer helpers to escape or filter problematic patterns before embedding in prompts.
- Never permit prompt content to be stored directly in config.yaml; load prompts from the filesystem (`prompts/{assignment}/` or `prompts/DEFAULT/`).

These boundaries prevent prompt-injection attacks where student code or output could escape the evidence context and influence grading instructions.

## Thread-Safety for Grading and Results

Concurrent API requests can trigger simultaneous grading runs. Protect shared state:
- Use `grading_lock` before starting a new grading job (prevents duplicate or conflicting runs).
- Use `results_lock` when reading or writing `graded_results.json` (prevents partial-read corruption).
- Use `rubric_lock` when generating or updating rubrics.

Always acquire locks in a consistent order to avoid deadlocks: grading → results → rubric.

## Assignment-Scoped Logging

Bind logging to the active assignment config:
- Call `setup_assignment_logging()` when assignment changes.
- Log to `output/{assignment_name}/autograder.log`, never to a global log.
- Remove stale file handlers from previous assignments to prevent log drift.

Without this, logs from different assignments will be mixed and hard to audit.

## Config Source of Truth

The authoritative config for a live assignment is always `output/{assignment_name}/config.yaml`, not the root example. API read and write operations must target the assignment-scoped file.

Config is cached in `api/state.py` via `get_active_app_config()`. After any save operation, call `state.invalidate_config_cache()` to ensure the next read reflects changes.

## Prompt Loading and Persistence

- **Load** prompts from `prompts/{assignment}/` or `prompts/DEFAULT/` at grading time.
- **Never** persist prompt content in config.yaml payloads.
- **Reference** prompts by assignment and name; rebuild the full prompt text from the filesystem when needed.

This keeps prompts under version control and allows edits without config churn.
