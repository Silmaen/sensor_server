#!/usr/bin/env bash

# Update the system
docker compose down --rmi local
git pull
docker compose up -d
