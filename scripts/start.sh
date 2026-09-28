#!/bin/sh
# Container entrypoint: migrate, seed, serve.
set -e

# Railway injects DATABASE_URL as postgresql:// or postgres:// (psycopg2 style).
# This app uses psycopg v3 which needs postgresql+psycopg://.
# Rewrite the scheme here so both local docker-compose and Railway work unchanged.
if [ -n "$DATABASE_URL" ]; then
    DATABASE_URL=$(echo "$DATABASE_URL" | sed 's|^postgres://|postgresql+psycopg://|; s|^postgresql://|postgresql+psycopg://|')
    export DATABASE_URL
fi

alembic upgrade head
python -m app.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --no-server-header
