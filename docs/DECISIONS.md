# Product decisions and simplification plan

This document records **explicit choices** for the AI autograder (previously implicit / vibe-coded). Update it when behavior or priorities change.

## Priority ranking (failure modes)

When tradeoffs conflict, use this order:

1. **Correctness of grades** — wrong scores, bad merges, export/Gradescope mismatches: **most important**.
2. **Privacy / leaks** — **not a focus** for this internal TA tool (still avoid obvious mistakes; no extra security theater).
3. **Cost / API spend** — **medium concern** — avoid accidental waste, but not the top priority.
4. **UX consistency** — errors, empty states, and API-vs-UI behavior should be **consistent and predictable**.

## Locked assumptions


| Topic                                | Decision                                                                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| **Audience**                         | Internal TAs only.                                                                                                                     |
| **API**                              | Exists **only** to serve the bundled static UI — not a public HTTP product.                                                            |
| **Deployment**                       | **One operator, one machine**, local `uvicorn`, browser UI. **Not** exposed to the internet by design.                                 |
| **Multi-user**                       | **Out of scope** — global locks and single active assignment are acceptable.                                                           |
| **Auth**                             | **None** (trusted local use).                                                                                                          |
| **Future: Gradescope servers**       | May **import this repo** and call functions programmatically — **later**; not designed yet.                                            |
| **CLI vs UI**                        | **Should behave the same** for pipeline steps; UI is primary for day-to-day testing.                                                   |
| **Config**                           | **YAML on disk is first-class** (especially regex); UI is additive.                                                                    |
| **Pipeline order**                   | Documented order (gather → parse → …) is **assumed**; no need for exotic branching.                                                    |
| **Re-parse**                         | **Overwrites** previous parsed outputs (keep).                                                                                         |
| **Parallel workers (`workers` > 1)** | **Important** — keep for speed.                                                                                                        |
| **Rubric review default**            | **On by default** (`rubric_review: true`).                                                                                             |
| **Temperature**                      | **As low as the model allows** — today `temperature_for_model()` uses **0** for non–GPT‑5 and **1** for GPT‑5 family (API constraint). |
| **Assignment names**                 | Filesystem sanitization under `output/{name}/` is **OK**.                                                                              |
| **Concurrent CLI + server**          | **Should not happen** — do not run two grading processes on the same `output/{assignment}/` folder; no cross-process file locking.   |
| **Tests**                            | **More coverage is better** — keep adding; mock LLMs in CI always.                                                                     |


## Data contracts

- **`graded_results.json`** — canonical store: JSON array of objects matching **`GradedResult`** (`results_models`). Changing fields requires coordinated updates to **export**, **UI**, and any scripts.
- **Gradescope export** — **`export.py`** builds per-student JSON with a **`tests`** array (plus optional format-lint test). The autograder ZIP copies precomputed files into Gradescope’s layout. Treat **export shape** as stable unless you version it.

## Simplification directions (status)

1. **Unify LLM retry policy** — **Done:** `rubric/generate_one.py` and `rubric/review_one.py` use `retry_with_exponential_backoff` with the same attempt count as `grade_group` (`MAX_VALIDATION_RETRIES + 1`).
2. **Unify graded-results loading** — **Done:** `load_results()` delegates to `load_results_with_backup()` (corrupt JSON → `*.broken` + log + `[]`).
3. **Unify estimate errors for the UI** — **Done:** estimate routes raise **HTTP 400** with string `detail`; `ui/js/shared.js` shows `detail` or legacy `error`.
4. **Deprecate `.env` key `key`** — **Done:** prefer `OPENAI_API_KEY`; `key` still works with `DeprecationWarning`.
5. **Document single-process assumption** — **Done:** this file + [docs/README.md](README.md); see **Concurrent CLI + server** above.

## Non-goals (for now)

- Public API, versioning, OAuth, multi-tenant UI.
- Replacing FastAPI unless there is a concrete benefit.
- Aggressive cost caps or quotas unless operational pain appears.

## Review checklist

When adding a feature, ask:

- Does it affect **grade correctness** or **export**? If yes, add or extend tests.
- Does it add a **second way** to do the same thing? Prefer one path (`pipeline_runner`, `get_assignment_output_paths`, `ensure_app_config`).
- Does the **UI** need a new error shape? Prefer `**HTTPException` + `detail`** consistent with other routes.

