#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Check if .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.example and configure it first."
    exit 1
fi

# Check if containers are currently running
RUNNING=$(docker compose ps -q 2>/dev/null | wc -l)

if [ "$RUNNING" -gt 0 ]; then
    echo "Stopping running containers..."
    docker compose down --rmi local
else
    echo "No containers running."
fi

echo "Pulling latest changes..."
git pull

echo "Building and starting services..."
docker compose up --build -d

echo "Waiting for services to become healthy..."
for i in $(seq 1 60); do
    UNHEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"unhealthy"\|"starting"' || true)
    HEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"healthy"' || true)
    TOTAL=$(docker compose ps -q 2>/dev/null | wc -l)

    if [ "$HEALTHY" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
        echo "All $TOTAL services healthy."
        docker compose ps
        exit 0
    fi
    sleep 2
done

echo "WARNING: Some services may not be healthy yet:"
docker compose ps
exit 1
