# Documentation index

Everything below lives under `docs/`. **Start here** so you do not bounce between overlapping files.

## Can you understand the codebase manually?

**Yes.** The code is large but **layered**: entry points (`app.py`, `main.py`) → thin wrappers (`pipeline_runner.py`, `api/state.py`) → pipeline modules (`gather`, `parse_notebook`, `grade`, `batch_grader`, `export`, `rubric/`) → shared config (`config_models`, `utils`).

Reasonable **first pass** (in order):

1. Repository **[README.md](../README.md)** — what the tool does and the default workflow.
2. **[DECISIONS.md](DECISIONS.md)** — who this is for, priorities, what is in scope (short).
3. **[CODEBASE_GUIDE.md](CODEBASE_GUIDE.md)** §1–2 — layout and **config** (assignment-scoped `output/{name}/config.yaml`).
4. Skim **§4 Pipeline stage reference** in the same file — one section per stage.
5. Pick your path: **Web** → §7 API; **CLI** → `main.py` + `pipeline_runner.py`; **grading** → `grade.py` + `batch_grader.py`.

Deep **product / accuracy / roadmap** discussion (optional): [AUTOGRADER_DESIGN_REVIEW.md](AUTOGRADER_DESIGN_REVIEW.md) Sections A–F only.

**Model choice and pricing** (optional): [OPENAI_VISION_MODELS.md](OPENAI_VISION_MODELS.md).

---

## Which document is which?

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | Project overview, workflow, quick commands — **not** the full technical spec. |
| [DECISIONS.md](DECISIONS.md) | Locked product assumptions and priorities (TA tool, single machine, API = UI, etc.). |
| [CODEBASE_GUIDE.md](CODEBASE_GUIDE.md) | **Main technical reference**: config, data flow, pipeline stages, API, tests, extension patterns. |
| [AUTOGRADER_DESIGN_REVIEW.md](AUTOGRADER_DESIGN_REVIEW.md) | **Optional**: effectiveness, bottlenecks, roadmap, KPIs — *not* day-to-day module docs. |
| [OPENAI_VISION_MODELS.md](OPENAI_VISION_MODELS.md) | Model comparison, temperature notes, pricing tables. |
| **This file** | How to read the docs + developer quick reference (below). |

There is **one** engineering tracker now: issues belong in your issue tracker or git history; we removed a stale local bug list.

**Keeping docs honest:** The **codebase is the source of truth**. [CODEBASE_GUIDE.md](CODEBASE_GUIDE.md) is updated when we notice drift (lock names, SSE routes, test file list, env vars). If you see a mismatch, trust `git` + the modules referenced — and fix the doc in the same PR when you can.

**Automation:** Cursor loads [`.cursor/rules/documentation-discipline.mdc`](../.cursor/rules/documentation-discipline.mdc) on every session — update the guide alongside code when behavior changes.

---

## Developer quick reference

### Formatting

Use **Black** (`black .`, line length 88). No Ruff/mypy gate required today.

### Configuration: dict vs `AppConfig`

- **`load_config(path) → dict`** — HTTP JSON, YAML round-trip, merging UI payloads (`PUT /config`).
- **`load_app_config(path) → AppConfig`** — pipeline code and CLIs (`main.py`, `grade.py`, `export.py`, …).
- **`ensure_app_config(x)`** — at boundaries where callers may pass dict or `AppConfig`.
- **Web app:** `get_active_config()` → dict; **`get_active_app_config()`** → `AppConfig` for `pipeline_runner`, `batch_grader`, `export`, etc.

### Environment

Set **`OPENAI_API_KEY`** in `.env` at the repo root. Legacy **`key`** is deprecated (see `utils.get_openai_client`).

### Token usage on graded results

- On disk, optional usage under **`_usage`**; canonical key: **`results_models.GRADED_RESULT_USAGE_KEY`** (also in `llm.usage_helpers`).
- Use **`llm.types.TokenUsage`** in code.
- Use **`detach_usage_from_graded_result(result)`** — do not hand-roll `pop("_usage")`.
- SSE summaries: **`graded_usage_summary_event(usage, model)`**.

### Common tasks

| Goal | Start here |
|------|------------|
| Grading prompt / JSON extraction | `prompt_builder.py`, `prompts/DEFAULT/*.md` |
| One student / one group | `grade.py` |
| Batch / resume / parallel | `batch_grader.py` |
| Config fields | `config_models.py`, `merge_partial_config_dict` |
| New FastAPI route | `api/routers/*.py`, `app.py` |
| Parse | `parse_notebook.py`, `parse_outputs.py`, `pipeline_runner.py` |
| Rubrics | `rubric/generate.py`, `rubric/review.py`, `rubric/prompts.py` |

### Tests

```bash
pytest tests/ -q
```

Patch **`get_active_config`** and **`get_active_app_config`** together when testing routes that use `get_active_app_config()` — see `tests/test_app.patch_active_assignment`. **Mock LLM calls** in tests; never hit the real API in CI.
