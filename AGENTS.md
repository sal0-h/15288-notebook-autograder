# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is an AI Autograder — an LLM-assisted grading pipeline for Jupyter notebook assignments. It has two entry points: a CLI (`main.py`) and a FastAPI web UI (`app.py`). No database, Docker, or external infrastructure is needed beyond an OpenAI API key.

### Running services

- **Web UI:** `python3 -m uvicorn app:app --reload --host 0.0.0.0 --port 8000` — serves both the API and static frontend at `http://localhost:8000`.
- **CLI pipeline:** `python3 main.py` — see `README.md` for flags and step names.

### Lint, test, format

- **Lint/format:** `python3 -m black --check .` (or `python3 -m black .` to auto-format). Config in `pyproject.toml` (line-length 88, target py311).
- **Tests:** `python3 -m pytest tests/ -q` — 227 tests, all mock LLM calls. No API key needed for tests.
- CI runs `black --check` and `pytest` on Python 3.10/3.11/3.12 (see `.github/workflows/`).

### Gotchas

- Use `python3` not `python` — the VM has no `python` symlink.
- `black` is not in `requirements.txt` or `requirements-dev.txt`; install it separately (`pip install black`) or via `pre-commit`.
- The `.env` file uses `key=sk-...` format (not `OPENAI_API_KEY=`); see `llm/client.py` for the lookup order: `.env` `key` field → `OPENAI_API_KEY` env var.
- The root `config.yaml` is only an example template — runtime configs live at `output/{assignment_name}/config.yaml`.
- Assignment output directories (`output/`) are gitignored; they appear at runtime when assignments are created.
