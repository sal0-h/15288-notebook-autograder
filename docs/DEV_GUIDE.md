# Developer guide

Short orientation for humans changing this repo. Deeper reference: [CODEBASE_GUIDE.md](./CODEBASE_GUIDE.md).

## Formatting

This project uses **Black** for Python formatting. If you use Black in your editor or run `black .` before committing, you stay aligned with the rest of the tree. (No Ruff/mypy gate is required today.)

## Configuration: dict vs `AppConfig`

- **`load_config(path) → dict`** — use when you need a plain dict: **HTTP JSON**, YAML round-trip, or merging UI payloads (`PUT /config`).
- **`load_app_config(path) → AppConfig`** — use for **pipeline code and repo CLIs** (`main.py`, `grade.py`, `export.py`, …) so you get typed fields (`cfg.parsed_dir`, `cfg.grading`, …) without ad hoc `.get()`.
- **`ensure_app_config(x)`** — accepts dict or `AppConfig`; use at boundaries when callers might pass either.
- **Web app:** `state.get_active_config()` returns a dict; **`state.get_active_app_config()`** returns `AppConfig` for routes that call `pipeline_runner`, `batch_grader`, `export`, etc.

Internal pipeline functions generally accept **`AppConfig | dict`** and normalize with `ensure_app_config` where callers still pass dicts (e.g. tests, merged payloads).

## Token usage on graded results

- On disk, optional usage lives under **`_usage`**. The canonical name is **`results_models.GRADED_RESULT_USAGE_KEY`** (also re-exported from **`llm`** / `llm.usage_helpers`).
- In code, use **`llm.types.TokenUsage`**.
- **Do not** hand-roll `pop("_usage")` in new code. Use **`detach_usage_from_graded_result(result)`** to get `(payload_for_disk, usage)` without mutating the original dict. For SSE-style summary events, use **`graded_usage_summary_event(usage, model)`**.

## Common tasks

| Goal | Start here |
|------|------------|
| Change grading prompt / JSON extraction | `prompt_builder.py`, `prompts/DEFAULT/*.md` |
| Change how one student is graded | `grade.py` (`grade_student`, `grade_group`) |
| Batch grading / resume / parallel | `batch_grader.py` |
| Add or change config fields | `config_models.py`, then defaults flow via `merge_partial_config_dict` in `config_models.py` |
| New FastAPI route | `api/routers/*.py`, register in `app.py` |
| Parse step behavior | `parse_notebook.py`, `parse_outputs.py`, `pipeline_runner.py` |
| Rubric generation / review | `rubric/generate.py`, `rubric/review.py`, `rubric/prompts.py` |

## Tests

```bash
pytest tests/ -q
```

Patch **`get_active_config` and `get_active_app_config` together** in API tests when a route uses `get_active_app_config()`; see `tests/test_app.patch_active_assignment`.
