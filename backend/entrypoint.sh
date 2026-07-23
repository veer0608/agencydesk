#!/bin/sh
set -e

echo "==> Running migrations on the dev database"
alembic upgrade head

echo "==> Seeding dev data"
python -m app.seed

echo "==> Preparing the test database"
OWNER_DATABASE_URL="$TEST_OWNER_DATABASE_URL" alembic upgrade head

echo "==> Starting API on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
