# WasteLens — Project Overview

Context document for handing off to another AI / collaborator to brainstorm
new features. Written from a full audit of the actual code (not just the
docs — see the "Docs vs. reality" note below, which matters).

## What this is

An enterprise waste-intelligence platform for a waste-sorting facility.
Households separate waste into four tagged bags (organic, polythene, paper,
general). At the facility, each bag is emptied onto a tray and photographed.
A computer-vision pipeline identifies every item on the tray — fine-grained
vegetable/food-scrap identification for organic waste, brand/product OCR for
packaging. Results build a longitudinal, per-household waste profile over
time (what they throw out, which brands, how much packaged food vs. fresh
food waste, etc.) for the facility's analysts.

## Architecture

| Component | Tech |
|---|---|
| API | FastAPI (Python 3.12), SQLAlchemy 2, Pydantic v2, Alembic |
| Worker | Celery on Redis — async CV analysis jobs, nightly profile rebuild |
| Database | PostgreSQL 16 (JSONB for raw model output) |
| Object storage | S3-compatible (MinIO locally, real S3 in prod) for tray images |
| Vision | NVIDIA NIM (default) or Anthropic, behind a `VisionProvider` interface — swappable, a `LocalYoloProvider` stub reserves a future self-hosted path |
| Frontend | React 18 + TypeScript + Vite, TanStack Query, Tailwind |
| Auth | JWT (PyJWT + bcrypt), roles: `admin`, `station_operator`, `reviewer`, `analyst` |
| Infra | Docker Compose (6 services: db, redis, minio, api, worker, frontend) |

Repo is on GitHub at `saadhiq/wastelens` (private).

## Domain model

```
Resident (table "users")          — a household; PII (name, phone, address); NOT a login
StaffAccount                      — a login account with a role; separate from Resident by design

Bag            — one resident's tagged bag (bag_type, tag_id/QR, status)
CollectionSession — one household pickup event, groups that day's bag captures
Capture        — one tray photo of one emptied bag (image_url, analysis_status)
Detection      — one item found on a tray by the CV pipeline

VocabularyItem — the allowed item_name values per bag_type (CV prompt vocabulary)
Brand          — brand names + aliases, fuzzy-matched against OCR text

UserWasteProfile — one row per (resident, ISO week); the aggregation output
AuditLog       — append-only: every PII read and every sensitive write
```

Relationships: `Resident 1—N Bag`, `Resident 1—N CollectionSession`,
`CollectionSession 1—N Capture`, `Bag 1—N Capture`, `Capture 1—N Detection`,
`Detection N—1 Brand` (nullable, via fuzzy OCR match).

Full field-level detail lives in `backend/app/models/*.py` — each file has a
docstring explaining the "why" of its shape, and `DECISIONS.md` records every
non-obvious call made where the original spec was ambiguous (11 numbered
decisions — worth reading in full before extending the domain model).

## API surface (all implemented, versioned under `/api/v1`)

**Auth** (`api/v1/auth.py`)
- `POST /auth/login`, `POST /auth/refresh` — JWT access/refresh tokens
- `GET /auth/me` — current account
- `POST /auth/staff` (admin) — create a staff account

**Residents** (`api/v1/residents.py`) — PII-gated
- `POST /users` / `GET /users/{id}` / `PATCH /users/{id}` (admin, station_operator — full record, every read audit-logged) / `DELETE /users/{id}` (admin)
- `GET /users` (any authenticated role) — anonymized list, no PII

**Bags & captures** (`api/v1/bags.py`, `api/v1/captures.py`)
- `POST /bags` (register a bag/QR tag → resident), `GET /bags/by-tag/{tag_id}`
- `POST /sessions`
- `POST /captures` (station_operator) — multipart tray-photo upload; stores to S3, creates the DB row, enqueues async CV analysis; `Idempotency-Key` header makes retries safe
- `GET /captures`, `GET /captures/{id}` (+detections) — station_operator, reviewer, analyst

**Analytics** (`api/v1/analytics.py`) — analyst/admin only, fully built
- `GET /profiles/{user_id}` — a household's weekly waste-profile timeline
- `POST /profiles/rebuild` — rebuild on demand (same job runs nightly via Celery beat)
- `GET /analytics/top-items`, `GET /analytics/top-brands` — most frequent (trustworthy) items/brands, filterable by bag_type/days
- `GET /analytics/quality` — model-health report: avg confidence, % needing review, capture failure rate, per-item-class breakdown (the "what to fine-tune first" report)

**Health** (`api/v1/health.py`)
- `GET /health/live` — process up
- `GET /health/ready` — checks DB, Redis, object storage reachability, returns 503 if any is down

## The CV pipeline (`services/analysis.py`, `services/vision/`)

`POST /captures` → upload to S3 → enqueue `analyze_capture_task` (Celery) →
worker: load active vocabulary for the bag's type from the DB → Redis daily
cost-cap check (`CV_DAILY_CALL_CAP`) → call the configured `VisionProvider`
with a bag-type-tailored prompt (`services/vision/prompts.py`) → parse+validate
the model's strict JSON contract, **one repair retry** if it's malformed →
fuzzy-match any OCR text against `Brand` (rapidfuzz) → write `Detection` rows,
flagging anything below `CONFIDENCE_REVIEW_THRESHOLD` as `needs_review` →
mark the capture `done`/`failed`.

Out-of-vocabulary model guesses are demoted to `unidentified_item` (original
guess preserved in `subcategory`) so aggregation only ever sees known labels
without silently losing data. On unrecoverable output the capture is marked
`failed` and the raw model text is kept on a placeholder detection row for
debugging. Verified against the real NVIDIA API during development, including
its retry behavior recovering from transient 500s.

## Aggregation (`services/aggregation.py`) — the "trustworthy detection" rule

A detection counts toward a resident's weekly profile only if it's
trustworthy: `confidence >= threshold` OR `review_status` is `confirmed`/
`corrected`, and never if `rejected`. A corrected label wins over the
model's original guess. **This is where the current gap actually is** — see
below.

## Docs vs. reality (worth knowing before trusting the README)

The README's "Build phases" checklist is **stale**:

| README says | Actually true |
|---|---|
| Phase 2 (review console + vocabulary/brand mgmt) — unchecked | **Correct, still not built.** No API endpoint exists to actually set a detection's `review_status`/`corrected_item_name` — those fields exist on the model and are *read* by analytics, but nothing ever *writes* them. No vocabulary/brand CRUD API either (only the one-time seed script). This is the real, current gap. |
| Phase 3 (aggregation + analytics) — unchecked | **Wrong — this is fully built.** `services/aggregation.py`, the `/analytics/*` + `/profiles/*` endpoints, and a real working `Analytics.tsx` dashboard (stat tiles, top-items/top-brands bar charts, per-item quality table, manual rebuild button) all exist and are tested (`tests/test_aggregation.py`, `tests/test_analytics_api.py`). |

So the **actual** state is: Phase 0 ✅, Phase 1 ✅, Phase 2 ❌ (the real
next thing), Phase 3 ✅, Phase 4 ❌ (exports, PII column-level encryption).

## Frontend (`frontend/src/pages/`)

- `Login.tsx` — JWT login
- `Station.tsx` — **real, working** tablet-optimized capture upload UI: pick/photograph an image, submit, poll the capture every 2.5s until analysis finishes, show detections
- `Analytics.tsx` — **real, working** dashboard consuming all the `/analytics/*` endpoints
- Review console — **placeholder only** ("Coming in a later phase") in `App.tsx`'s router; matches the Phase 2 gap above

## Auth & RBAC

JWT access/refresh (`core/security.py`). `require_roles(*roles)` dependency
factory (`api/deps.py`) — admin is always allowed. `PII_ROLES = (admin,
station_operator)` gates resident PII reads, each logged to `audit_log` via
`services/audit.py`.

## Repository layout

```
backend/app/
  api/v1/       versioned REST endpoints (one file per resource)
  core/         JWT/RBAC primitives, structured logging (structlog)
  models/       SQLAlchemy domain model
  schemas/      Pydantic request/response models
  seeds/        vocabulary/brand/admin bootstrap script
  services/     business logic — analysis pipeline, aggregation, vision providers, audit, storage
  worker.py     Celery app + tasks
backend/alembic/   migrations (hand-written initial schema, autogenerate works from here on)
backend/tests/     pytest, 39 tests, 77% coverage
frontend/src/
  pages/        Login, Station, Analytics (Review is a stub)
  lib/api.ts    typed fetch wrapper
```

## Running it locally

```bash
docker compose up --build
# API + docs:  http://localhost:8000/docs
# Frontend:    http://localhost:5173
docker compose exec api python -m app.seeds.seed   # taxonomy, brands, bootstrap admin
```
Log in with `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD` from `.env`.

## Where feature ideas would land (the real gaps)

1. **Phase 2 — Review console** (the biggest concrete gap): a
   `POST /detections/{id}/review` action endpoint (confirm/correct/reject)
   and vocabulary/brand CRUD endpoints, plus the actual frontend Review page.
   Without this, `review_status` never leaves `unreviewed` in practice, which
   quietly weakens the aggregation "trustworthy detection" rule to
   confidence-threshold-only.
2. **Phase 4 — Exports + hardening**: CSV/PDF exports for analysts,
   column-level PII encryption (deferred per `DECISIONS.md` #2 — currently
   role-gated + audited, not encrypted at rest).
3. **Vision pipeline**: the `LocalYoloProvider` stub (self-hosted
   detection instead of a hosted vision API) is scaffolded but unimplemented.
4. Anything beyond the original spec is fair game — this doc plus
   `DECISIONS.md` should be enough context for a fresh brainstorm.


## Extension work — rules for every session

1. This is an EXISTING, WORKING codebase. Extend it. Do not restructure it,
   do not rename existing tables or columns, do not "modernise" working code.
2. The table `users` holds Residents. That name stays. Do not rename it.
3. `services/analysis.py` and `services/vision/` are working and verified
   against the real NVIDIA API. Change them ONLY where a phase explicitly says
   to. Never refactor them opportunistically.
4. Follow existing conventions exactly:
   - One file per resource under `api/v1/`
   - Business logic in `services/`, never in endpoint functions
   - Pydantic v2 schemas in `schemas/`, SQLAlchemy models in `models/`
   - `require_roles(...)` from `api/deps.py` for RBAC
   - Every PII read and sensitive write goes through `services/audit.py`
   - Model files carry a docstring explaining the "why" of their shape
5. Every non-obvious call goes in `DECISIONS.md` as a new numbered entry,
   in the existing format. This is how we stay consistent across sessions.
6. Alembic: autogenerate the migration, then READ it before applying. Never
   apply a migration you have not reviewed. New columns on existing tables
   must be nullable or carry a server default — there is existing data.
7. Money and weights are Decimal/Numeric. Never Float.
8. Timestamps are timezone-aware UTC in the DB, displayed Asia/Colombo (+05:30).
9. Never silently drop data. Unmapped labels, failed model output and
   low-confidence detections are all preserved, never discarded.
10. Work on ONE PHASE at a time. Restate scope and assumptions first, wait for
    my confirmation, then implement, then STOP and summarise. Never begin the
    next phase.
11. Tests are mandatory: pytest, matching the existing style in backend/tests/.
    Coverage must not drop below its current level.
