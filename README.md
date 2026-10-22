# AgentFlow

**A workflow automation API built with FastAPI, Celery, and Redis.**

AgentFlow executes multi-step workflows, records execution status and step traces, supports reusable templates and scheduling, and exposes a REST API with optional API-key authentication and rate limiting.

## Features
- FastAPI REST API with interactive Swagger documentation
- Background workflow execution using Celery and Redis
- Persistent workflow runs, step traces, and reusable templates
- Workflow cancellation and scheduling
- Request IDs, structured logging, and rate limiting
- Optional AI-assisted workflow planning
- Automated tests with pytest

## Quick start with Docker
Prerequisites: Docker Desktop with Docker Compose v2.

1. Clone the repository and enter the project directory.
2. Create your local environment file:
   ```powershell
   Copy-Item .env.example .env
   ```
3. Edit `.env`. Add any optional AI provider key. To protect the API, set `AGENTFLOW_API_KEY` to a strong random secret and keep it private.
4. Build and start the API, worker, and Redis:
   ```bash
   docker compose up --build -d
   ```
5. Check service status and logs:
   ```bash
   docker compose ps
   docker compose logs -f api worker
   ```
6. Open http://localhost:8000/docs.

Health endpoints:
- `GET /health/live` — API process liveness
- `GET /health/ready` — database and Redis readiness

The Compose setup keeps the SQLite database and Redis data in named Docker volumes. The API and worker share the database volume.

## Stop and restart
```bash
docker compose down
docker compose up -d
```
`docker compose down` preserves named volumes. To delete all persisted data, including workflows and Redis data, use `docker compose down -v` (destructive).

## Run tests locally
```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

## Local development without Docker
```bash
python -m venv .venv
```
Windows CMD:
```bat
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```
Run Celery in another terminal:
```bash
python -m celery -A app.celery_app:celery_app worker --loglevel=info --pool=solo
```
For local development, ensure Redis is running and `REDIS_URL` in `.env` points to it.

## Configuration and security
- Never commit `.env` or provider/API secrets.
- Keep `DEBUG=false` in deployed environments.
- `AGENTFLOW_API_KEY` is optional; when configured, send it in the `X-API-Key` header for protected endpoints.
- Compose uses SQLite for simple single-host deployment. Migrate to PostgreSQL before higher-concurrency or multi-instance production.
- Do not expose Redis directly to the public internet.

## Tech stack
Python · FastAPI · Celery · Redis · SQLAlchemy · SQLite · pytest
