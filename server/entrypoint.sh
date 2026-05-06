#!/bin/bash
set -e

# Alembic needs a sync SQLite URL (no +aiosqlite driver).
# Derive it from DATABASE_URL by stripping the async driver suffix.
DB_URL="${DATABASE_URL:-sqlite+aiosqlite:////data/syncarr.db}"
export SYNCARR_DATABASE_URL="${DB_URL/+aiosqlite/}"

echo "Running database migrations..."
alembic upgrade head

echo "Starting server..."
exec uvicorn syncarr_server.main:app --host 0.0.0.0 --port 8000
