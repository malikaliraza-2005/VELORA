# backend/

Python 3.12 + FastAPI service: webhook ingress and background workers (decision AD-15).

In P1 this is a skeleton only: an app factory with a health endpoint, settings loaded from
environment variables, and an empty `workers` package. Product features arrive in later
phases — see [`../docs/planning/implementation-plan.md`](../docs/planning/implementation-plan.md).

## Local development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
cd backend
uv sync                      # create .venv from the committed lockfile
uv run ruff format --check . # formatting
uv run ruff check .          # lint
uv run mypy                  # type check (strict)
uv run pytest                # tests
```
