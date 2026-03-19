---
description: "Use when writing or editing tests. Covers pytest fixtures, tmp_path isolation, mock OpenAI clients, regression-sensitive behaviors, and test organization for integration and units tests."
name: "Testing Guidelines"
applyTo: "tests/test_*.py"
---

# Testing Guidelines

## Pytest Fixtures and Conftest

`tests/conftest.py` adds the project root to `sys.path` for imports. Tests construct their own setup:
- Use pytest's built-in `tmp_path` for temporary assignment directories.
- Build minimal config dicts inline with required fields (e.g. `model`, `grading.question_groups`, `rubrics`); use `DEFAULT_MODEL` from utils.
- Mock OpenAI via `unittest.mock.patch` on the relevant client or completion methods.

Fixtures ensure tests are isolated and do not pollute the real `output/` directory.

## Isolation with tmp_path

Always use `tmp_path` (pytest built-in) for output artifacts:
- Create temporary assignment directories under `tmp_path`.
- Write config, submissions, parsed data, and results to temp locations.
- Leave real `output/` untouched so tests can run in parallel without conflicts.

**Example**:
```python
def test_grade_student(tmp_path):
    assignment_dir = tmp_path / "LabTest_3_S26"
    assignment_dir.mkdir(parents=True)
    config = {"output_dir": str(assignment_dir), "model": DEFAULT_MODEL, "grading": {"question_groups": [["1.1"]], "grade_only": None}, "rubrics": {}}
    # Grade into tmp_path, not output/
```

## Mock OpenAI Clients

For tests involving LLM calls, use `mock_openai_client` fixture to avoid API costs and network latency:
- Mock returns deterministic, consistent grading responses.
- Validate that prompts sent to the mocked client have expected structure.
- Do not make real OpenAI API calls in tests (use integration tests sparingly for that).

## Regression-Sensitive Behaviors

These behaviors should be tested continuously to catch silent regressions:

### Incremental Save and Resume
- Verify that interrupted runs can resume without re-grading completed students.
- Verify that `graded_results.json` is written after each student batch.
- Test partial reruns with `grade_only` and `grade_only_merge` semantics.

### Config Merging
- Verify that partial API updates do not overwrite unspecified fields.
- Test that root config.yaml is never the runtime source of truth.

### Artifact Scoping
- Verify that all output goes to `output/{assignment_name}/`, not root.
- Verify logging binds to the assignment-scoped log file.

### Export Schema Stability
- Verify that Gradescope JSON export preserves the expected `tests` array structure.
- Test that optional fields like `output_format` do not break downstream tools.

## Test Organization

### Unit Tests
- Test individual functions in isolation (parse_notebook, rubric generation, grading logic).
- Mock external dependencies (OpenAI, file I/O for data-heavy operations).
- Fast and focused on behavior.

### Integration Tests
- Test multi-stage pipelines (parse → grade → export).
- Use realistic configs and sample submissions from the repo.
- Verify that pipeline steps correctly chain outputs and preserve invariants.

### API Tests
- Test FastAPI endpoints with realistic partial payloads.
- Verify concurrent access and lock behavior.
- Validate error responses and edge cases (missing assignment, malformed config).

## Test Cleanup

Always clean up after tests:
- Use `tmp_path` fixtures to auto-clean temporary directories.
- Mock OpenAI to avoid side effects.
- Reset assignment-scoped logging to prevent handler leaks.

If a test modifies global state (e.g., logging), use a fixture that restores the original state after the test.

## Running Tests

```bash
# Full suite
.venv/bin/python -m pytest tests/ -q

# Focused unit tests
.venv/bin/python -m pytest tests/test_grade.py tests/test_parse.py -q

# Integration pipeline
.venv/bin/python -m pytest tests/test_integration.py -q

# API and concurrency
.venv/bin/python -m pytest tests/test_app.py -q
```

Run focused tests while editing to catch regressions early.
