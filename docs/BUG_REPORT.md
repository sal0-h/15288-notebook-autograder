# Engineering Issues Report — AI Autograder

This report tracks current engineering issues across:
- Bugs
- Fragility / maintainability concerns
- Inconsistencies

Status values:
- `Open`: should be fixed in code
- `Accepted`: known design trade-off for now
- `Resolved`: already fixed

## 1) Bugs

| ID | Severity | File | Status | Summary |
|----|----------|------|--------|---------|
| E-001 | High | `parse_notebook.py:291`, `grade.py:412`, `rubric.py:458`, `export.py:275`, `calibrate.py:84`, `gather.py:180` | Open | Standalone script defaults still use root `config.yaml` despite assignment-scoped runtime config model |
| E-002 | Medium | `app.py:713`, `grade.py:351` | Open | Single-student regrade path can persist internal `_usage` field into `graded_results.json` |
| E-003 | Low | `app.py:373` | Open | `/gather` reads full upload into memory before enforcing size limit |
| E-004 | Medium | `utils.py:135`, `utils.py:188` | Open | Missing config file is treated as empty config, which can silently resolve to default assignment paths |

### E-001 — Standalone script config default mismatch

Most module CLIs still default to `Path("config.yaml")`, while runtime behavior is
assignment-scoped under `output/{assignment_name}/config.yaml`.

Impact:
- Running scripts without explicit `--config` can resolve paths incorrectly.
- With `load_config`, root-level `config.yaml` causes project-root inference from the
  wrong directory depth.

Recommended fix:
- Change all standalone defaults to `None` and require explicit `--config`, or
- Use a helper that resolves to active assignment config under `output/{assignment}/config.yaml`.

### E-002 — `_usage` schema leak in single-student regrade

`grade_student()` may include `_usage` (`grade.py:351`).
`batch_grader.py` removes it before persistence, but `api_grade_one` writes `result`
directly to `graded_results.json` without removing `_usage`.

Impact:
- Inconsistent persisted schema for results.
- Unexpected keys in downstream tools/manual consumers.

Recommended fix:
- In `api_grade_one`, call `result.pop("_usage", None)` before writing.

### E-003 — Upload memory pressure in `/gather`

`api_gather` currently does:

```python
content = await zip_file.read()
if len(content) > upload_max_bytes:
    ...
```

Impact:
- Request size is enforced only after full buffering in RAM.

Recommended fix:
- Stream-upload in chunks with early abort once threshold is exceeded.

### E-004 — Missing config file silently falls back to defaults

`_read_yaml_dict()` returns `{}` when config path does not exist.
`load_config()` then fills defaults and computes output paths.

Impact:
- Typos in config path can silently target default assignment/output paths.

Recommended fix:
- Raise `FileNotFoundError` in `load_config()` when assignment config is missing,
  except in explicit "create" flow (`/load-or-create`).

## 2) Fragility and Simplification Opportunities

| ID | Severity | File | Status | Summary |
|----|----------|------|--------|---------|
| E-101 | Medium | `export.py` embedded `RUN_AUTOGRADER` | Open | Name matching falls back to substring/startswith and may map to wrong student file |
| E-102 | Low | `app.py:321` | Accepted | Solution notebook upload path is outside `output/{assignment}` tree by design |
| E-103 | Low | `app.py:194` | Open | `PUT /config` falls back to empty existing config on any load exception |
| E-104 | Low | `batch_grader.py:36`, `batch_grader.py:243` | Open | Parallel grading shares one OpenAI client object across worker threads |

### E-101 — Fragile name matching in Gradescope autograder script

The generated `run_autograder` first tries exact match, then falls back to
`startswith` / substring matching.

Impact:
- Ambiguous matches for similar names can return wrong precomputed result.

Recommended fix:
- Export a deterministic lookup index (e.g., metadata key -> JSON filename)
  and use exact lookup only.

### E-102 — Solution notebook stored outside assignment output tree

`/parse-solution-upload` stores to `{project_root}/{assignment_name}/{assignment_name}_sol.ipynb`.

Impact:
- Mixed artifact layout can confuse operators.

Reason accepted:
- Current behavior is intentional and functional; docs now explicitly mention this.

### E-103 — Broad exception handling in `PUT /config`

`api_put_config` does:

```python
try:
    existing = _get_active_config()
except Exception:
    existing = {}
```

Impact:
- Corrupted config can be masked and overwritten with defaults + partial payload.

Recommended fix:
- Distinguish expected "no active assignment" from other errors.
- Return 422/500 for malformed active config instead of silently resetting.

### E-104 — Shared client object in parallel grading

`grade_all_students` creates one OpenAI client and passes it into all worker calls.

Impact:
- Potential thread-safety/performance uncertainty.

Recommended fix:
- Initialize one client per worker task (or document client thread-safety assumption).

## 3) Inconsistencies

| ID | Severity | Area | Status | Summary |
|----|----------|------|--------|---------|
| E-201 | Medium | Docs vs implementation | Resolved | README and CODEBASE_GUIDE were updated to match assignment-scoped config behavior and prompt handling |
| E-202 | Medium | Data shape consistency | Open | Batch grading strips `_usage`; single-student regrade currently does not |

### E-201 — Documentation drift

Previously, docs contained stale claims around config update behavior, prompt message
shape details, and parallel-client behavior.

Current status:
- README and CODEBASE_GUIDE updated in this pass.

### E-202 — Result schema consistency

`batch_grader.py` normalizes persisted results by removing `_usage`; `api_grade_one`
currently does not.

Current status:
- Still open (see E-002).

## 4) Recently Resolved Items

| ID | Status | Note |
|----|--------|------|
| B-01 | Resolved | `from __future__ import annotations` added in `utils.py` |
| B-02 | Resolved | Root `config.yaml` indentation fixed |
| B-03 | Resolved | `app.py` imports organized at top |
| B-04 | Resolved | `_setup_file_logging` docstring corrected |
| B-05 | Resolved | Dead `prompts` filtering removed from `save_config` |
| B-09 | Resolved | Duplicate `_apply_config_defaults` call removed |
| B-10 | Resolved | Rubric generation now checks `solution_parsed.json` before API client creation |
