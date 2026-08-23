# WasteLens

Enterprise waste-intelligence platform. Households separate waste into four
tagged bags (organic, polythene, paper, general); at the sorting facility each
bag is emptied onto a tray and photographed; a CV pipeline detects every item
(fine-grained vegetables for organic, brand/product OCR for packaging); results
build longitudinal per-household waste profiles.

## Architecture

| Component | Tech |
|---|---|
| API | FastAPI (Python 3.12), SQLAlchemy 2, Pydantic v2, Alembic |
| Worker | Celery on Redis (async CV analysis jobs) |
| Database | PostgreSQL 16 (JSONB for raw detections) |
| Object storage | S3-compatible (MinIO locally) for tray images |
| Vision | NVIDIA NIM (Llama 4 Maverick, default) or Anthropic — behind a `VisionProvider` interface, swappable for self-hosted YOLO/SAM in Phase 2 |
| Frontend | React 18 + TypeScript + Vite, TanStack Query, Tailwind |
| Auth | JWT, roles: `admin`, `station_operator`, `reviewer`, `analyst` |

## Quick start

```bash
cp .env.example .env        # then edit secrets (JWT_SECRET, passwords, NVIDIA_API_KEY)
docker compose up --build
```

Services:
- API + OpenAPI docs: http://localhost:8000/docs
- Frontend: http://localhost:5173
- MinIO console: http://localhost:9001

Migrations run automatically when the api container starts. Seed the taxonomy,
brands, and bootstrap admin:

```bash
docker compose exec api python -m app.seeds.seed
```

Log in with `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` from your
`.env` via `POST /api/v1/auth/login`.

## Development

Backend (requires Python 3.12):

```bash
cd backend
pip install -e ".[dev]"
ruff check . && mypy app
pytest                       # DB-backed tests need Postgres; unit tests run anywhere
```

Tests use `TEST_DATABASE_URL` (default: local compose Postgres, database
`wastelens_test` — create it once with
`docker compose exec db createdb -U wastelens wastelens_test`). DB-backed tests
skip automatically when no database is reachable.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Pre-commit hooks:

```bash
pip install pre-commit && pre-commit install
```

## Repository layout

```
backend/
  app/
    api/v1/       # versioned REST endpoints
    core/         # security (JWT/RBAC), structured logging
    models/       # SQLAlchemy domain model (see DECISIONS.md)
    schemas/      # Pydantic request/response models
    seeds/        # vocabulary/brand/admin seed script
    services/     # business logic (audit; CV pipeline in Phase 1)
    worker.py     # Celery app
  alembic/        # migrations
  tests/
frontend/         # React app (station capture / review / analytics shells)
```

## Build phases

- [x] **Phase 0 — Scaffold**: repo, Docker Compose, schema + migrations, auth/RBAC, health checks, CI
- [x] **Phase 1 — Capture pipeline**: upload → queue → NVIDIA/Anthropic VisionProvider → detections (demo: `python backend/scripts/demo.py`)
- [ ] **Phase 2 — Review console** + vocabulary/brand management (detections can be flagged `needs_review` but nothing yet writes `review_status`/`corrected_item_name` — no review action endpoint or UI exists)
- [x] **Phase 3 — Aggregation job** + waste profiles + analytics dashboard (`/analytics/*`, `/profiles/*`, `Analytics.tsx`)
- [ ] **Phase 4 — Exports** + audit hardening (PII encryption at rest)

Assumptions and trade-offs are recorded in [DECISIONS.md](DECISIONS.md). See
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) for a full architecture writeup.
