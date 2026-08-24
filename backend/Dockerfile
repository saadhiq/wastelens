FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libzbar0: shared library pyzbar needs at runtime (Phase 5 barcode decode) —
# pip alone doesn't provide it, unlike Windows' bundled-DLL wheel.
# postgresql-client: provides pg_dump for the nightly backup job (Phase 8).
# Debian's default package version trails the db service's postgres:16 image
# by a major version or two; that's fine for pg_dump against a plain schema
# like this one, but if the server image is ever bumped well past this,
# revisit pinning an exact-matching client via the PGDG apt repo.
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install deps from project metadata (source copied after, for layer caching)
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Reinstall now that source is present so the package resolves
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
