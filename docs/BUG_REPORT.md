# Bug Report — AI Autograder

This document records bugs, unintended behaviours, and notable code-quality issues
found during a codebase review.  Each entry includes severity, location, root cause,
and fix status.

---

## Summary

| ID | Severity | File | Status |
|----|----------|------|--------|
| [B-01](#b-01-typeror-dictappconfig-forward-reference-blocks-all-imports) | **Critical** | `utils.py:215` | ✅ Fixed |
| [B-02](#b-02-rootconfig-yaml-uses-tab-indentation-invalid-yaml) | **High** | `config.yaml` | ✅ Fixed |
| [B-03](#b-03-imports-scattered-throughout-apppy-non-pep-8) | **Medium** | `app.py:1–80` | ✅ Fixed |
| [B-04](#b-04-misleading-docstring-on-_setup_file_logging) | **Low** | `app.py:24` | ✅ Fixed |
| [B-05](#b-05-save_config-filters-prompts-key-that-can-never-exist-dead-code) | **Low** | `utils.py:242` | ⚠️ Documented |
| [B-06](#b-06-full-file-read-before-size-check-in-gather-endpoint) | **Low** | `app.py` `/gather` | ⚠️ Documented |
| [B-07](#b-07-_active_config_path-global-is-not-concurrency-safe) | **Low** | `app.py` | ⚠️ Documented |
| [B-08](#b-08-solution-notebook-stored-outside-output-directory) | **Info** | `app.py` `/parse-solution-upload` | ⚠️ Documented |
| [B-09](#b-09-_apply_config_defaults-called-twice-in-load_config) | **Info** | `utils.py` | ⚠️ Documented |
| [B-10](#b-10-generate_rubrics-checks-api-key-before-solution-file-existence) | **Medium** | `rubric.py:349` | ✅ Fixed |

---

## B-01 — TypeError: `dict | "AppConfig"` forward reference blocks all imports

**Severity:** Critical  
**File:** `utils.py`, line 215  
**Status:** ✅ Fixed (added `from __future__ import annotations`)

### Symptom

Every test file and every module that imports from `utils` fails at import time:

```
TypeError: unsupported operand type(s) for |: 'type' and 'str'
```

### Root cause

Python evaluates function annotations at *definition time* (unless `from __future__
import annotations` is present, which makes all annotations lazy strings).

The function signature:

```python
def save_config(config: dict | "AppConfig", config_path: Path) -> None:
```

tries to evaluate `dict | "AppConfig"` at module load time.  In Python 3.10+ the
`|` union operator works for runtime type expressions (`int | str` creates a
`types.UnionType`), but only when both operands are actual types.  Here `"AppConfig"`
is a *string* (a forward reference), so `dict.__or__("AppConfig")` raises a
`TypeError`.

### Fix

Added `from __future__ import annotations` as the second line of `utils.py`.
This makes Python treat all annotations as implicitly quoted strings, evaluated
lazily only when `get_type_hints()` is explicitly called — which is the behaviour
Pydantic v2 already expects.

---

## B-02 — Root `config.yaml` uses tab indentation — invalid YAML

**Severity:** High  
**File:** `config.yaml`  
**Status:** ✅ Fixed (replaced tabs with two-space indentation)

### Symptom

```
yaml.scanner.ScannerError: found character '\t' that cannot start any token
  in "config.yaml", line 18, column 1
```

Calling `yaml.safe_load()` on the root `config.yaml` raises a scanner error.

### Root cause

The YAML specification explicitly forbids tab characters as indentation.  The
`parsing:` and `grading:` sections were indented with a literal `\t` tab instead
of spaces.

### Impact

The file is documented as an *example only* (the web UI does not load it
automatically).  However:

* Any user who copies the file as a starting point receives a broken config.
* CI/automation that validates config examples would fail.

### Fix

Replaced all tab indentation with two-space indentation.

---

## B-03 — Imports scattered throughout `app.py` (non-PEP 8)

**Severity:** Medium  
**File:** `app.py`, lines 1–80  
**Status:** ✅ Fixed (all imports moved to the top of the file)

### Symptom

The original `app.py` had this structure:

```
line  1: standard library imports  (asyncio, logging, …)
line  8: module globals            (logger, _active_config_path)
line 13: function definitions      (_get_active_config, _setup_file_logging)
           ↑ these use HTTPException, Path, load_config — NOT YET IMPORTED
line 39: more standard imports     (io, json, tempfile, …)
line 47: module globals            (_grading_lock, …)
line 55: third-party imports       (fastapi, sse_starlette)
line 60: import re
line 62: local imports             (utils, gather, …)
```

`_get_active_config` references `HTTPException` and `load_config` before they are
imported.  `_setup_file_logging` references `Path` and `setup_assignment_logging`
before they are imported.  This works at runtime (function bodies are executed
only when called, not when defined), but:

* It violates [PEP 8 §Imports](https://peps.python.org/pep-0008/#imports).
* It makes the dependency graph opaque and easy to break.
* Static analysers and type checkers report spurious "undefined name" errors.

### Fix

All imports were moved to the top of the file, grouped as:
1. Standard library
2. Third-party (`fastapi`, `sse_starlette`)
3. Local project modules

Module-level globals and helper functions now follow the imports.

---

## B-04 — Misleading docstring on `_setup_file_logging`

**Severity:** Low  
**File:** `app.py`, line 24  
**Status:** ✅ Fixed

### Original docstring

```python
def _setup_file_logging() -> None:
    """Ensure the root logger writes to the active assignment's autograder.log."""
```

### Problem

The function does **not** configure the *root* logger.  It calls
`setup_assignment_logging(assignment_name, out_dir)` which configures a logger
named `autograder.{assignment_name}`.  The root logger is untouched.  Any log
messages emitted via `logger` (which is `logging.getLogger("app")`) are still
handled by whatever handlers the root logger has at runtime, not by the
assignment file handler.

### Fix

Docstring updated to:

```python
def _setup_file_logging() -> None:
    """Configure the assignment-specific logger to write to the active assignment's autograder.log."""
```

---

## B-05 — `save_config` filters `prompts` key that can never exist (dead code)

**Severity:** Low  
**File:** `utils.py`, line 242  
**Status:** ⚠️ Documented (not fixed — harmless)

### Code

```python
out_cfg = {k: v for k, v in cfg.items() if k not in ("prompts",)}
```

### Problem

`cfg` at this point is the result of `app_config_to_yaml_data(validated)`, which
calls `AppConfig.model_dump(...)`.  `AppConfig` is declared with
`model_config = ConfigDict(extra="ignore")`, so any extra fields (including a
hypothetical `prompts` key) are silently dropped during `model_validate` / at
construction time.  The `prompts` key can never survive into `cfg`.

The filter is therefore dead code — it can never actually filter anything.

### Recommendation

Either:

* Remove the filter (simplify the line to `out_cfg = cfg`), or
* If `prompts` is a field that may be added to the schema in the future, add it to
  `AppConfig` with a proper type.

---

## B-06 — Full file read before size check in `/gather` endpoint

**Severity:** Low  
**File:** `app.py`, `/gather` endpoint  
**Status:** ⚠️ Documented (accepted design trade-off)

### Code

```python
content = await zip_file.read()          # reads entire file into RAM
if len(content) > upload_max_bytes:      # then checks size
    raise HTTPException(status_code=413, …)
```

### Problem

The server reads the entire uploaded file into memory before checking whether it
exceeds the 500 MB limit.  An attacker (or a user with a misconfigured client)
can force the server to allocate up to 500 MB + 1 byte before the request is
rejected.

### Recommendation

Use a streaming read that aborts once the size limit is exceeded:

```python
chunks = []
total = 0
async for chunk in zip_file:
    total += len(chunk)
    if total > upload_max_bytes:
        raise HTTPException(status_code=413, …)
    chunks.append(chunk)
content = b"".join(chunks)
```

Alternatively, configure a global request-body size limit at the ASGI/server layer
(e.g., via `uvicorn --limit-concurrency` or a reverse proxy like nginx).

---

## B-07 — `_active_config_path` global is not concurrency-safe

**Severity:** Low  
**File:** `app.py`, global `_active_config_path`  
**Status:** ⚠️ Documented (accepted single-assignment design)

### Problem

`_active_config_path` is a module-level global that is mutated by
`POST /load-or-create`.  Under concurrent load (e.g., two browser tabs calling
`/load-or-create` simultaneously with different assignment names), the second
write would silently redirect the active assignment for the first caller mid-flight.

### Recommendation

This is a known design constraint — the app is designed for single-user, single-
assignment use at a time.  The existing architecture (one active assignment
globally) is acceptable for this use case, but should be documented prominently
so future contributors do not add multi-tenant features that rely on the same
global.

See `docs/CODEBASE_GUIDE.md` §7 for more detail on the intended usage pattern.

---

## B-08 — Solution notebook stored outside `output/` directory

**Severity:** Info  
**File:** `app.py`, `/parse-solution-upload` endpoint  
**Status:** ⚠️ Documented (intentional design)

### Behaviour

Uploaded solution notebooks are saved to:

```
{project_root}/{assignment_name}/{assignment_name}_sol.ipynb
```

while all other per-assignment artefacts (config, parsed JSON, graded results,
Excel export, logs) are stored under:

```
{project_root}/output/{assignment_name}/
```

### Notes

This appears intentional — the solution notebook is instructor-owned reference
material and is deliberately kept separate from student output artefacts.

The path is stored as a relative path in `config.yaml` (e.g.,
`MyLab/MyLab_sol.ipynb`) and resolved correctly by `load_config`.

However, this creates a top-level `{assignment_name}/` directory alongside
`output/`, which can be confusing.  The `.gitignore` should exclude these
directories if the project is under version control.

---

## B-09 — `_apply_config_defaults` called twice in `load_config`

**Severity:** Info  
**File:** `utils.py`, `load_config` function  
**Status:** ⚠️ Documented (harmless)

### Code

```python
def load_config(config_path: Path) -> dict:
    cfg = _read_yaml_dict(path)
    cfg = _apply_config_defaults(cfg)          # ← first call
    # … path resolution …
    cfg = _apply_config_defaults(cfg)          # ← second call (redundant)
    return ensure_app_config(cfg).model_dump()
```

`_apply_config_defaults` is idempotent (it uses `setdefault`), so calling it
twice is harmless.  But the second call adds unnecessary work.

### Recommendation

Remove the second call to `_apply_config_defaults` now that path resolution no
longer introduces new keys that need defaults.

---

*Report generated during codebase review on 2026-03-13.*

---

## B-10 — `generate_rubrics` checks API key before solution file existence

**Severity:** Medium  
**File:** `rubric.py`, line 349  
**Status:** ✅ Fixed (moved `get_openai_client()` call after file existence check)

### Symptom

The test `test_missing_solution_raises` expects:

```
FileNotFoundError: Solution parsed not found
```

but receives:

```
ValueError: API key not found. Set 'key=your-api-key' in .env or OPENAI_API_KEY …
```

### Root cause

`generate_rubrics` called `get_openai_client()` as its first action, before
checking whether `solution_parsed.json` exists:

```python
if client is None:
    client = get_openai_client()    # ← always executed first

# … later …
if not solution_path.exists():
    raise FileNotFoundError(…)     # ← this would be the right first error
```

When the solution file is missing AND no API key is configured (e.g., in CI or
test environments), the `ValueError` from `get_openai_client()` masks the
more-specific and more-actionable `FileNotFoundError`.

### Fix

Moved the `get_openai_client()` call to after the solution file existence check:

```python
output_dir = Path(cfg.output_dir)
solution_path = output_dir / "solution_parsed.json"

if not solution_path.exists():            # ← checked first
    raise FileNotFoundError(…)

if client is None:
    client = get_openai_client()          # ← only if file exists
```

This ensures the most specific precondition failure is reported first, and avoids
an unnecessary API-key check when the prerequisite file is missing.
