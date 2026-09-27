#!/bin/sh
# Container entrypoint: migrate, seed, serve.
set -e
alembic upgrade head
python -m app.seed
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --no-server-header
