#!/bin/bash
set -e

echo "Waiting for database..."
python << 'EOF'
import time, os, psycopg
dsn = f"host=timescaledb dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']}"
for i in range(30):
    try:
        conn = psycopg.connect(dsn)
        conn.close()
        print("Database ready.")
        break
    except Exception:
        time.sleep(1)
else:
    print("Database not available after 30s")
    exit(1)
EOF

echo "Compiling translations..."
python manage.py compilemessages --ignore=.venv 2>/dev/null || true

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Running migrations..."
python manage.py migrate --noinput

echo "Creating superuser if needed..."
python manage.py ensure_superuser

mkdir -p /app/logs

exec "$@"
