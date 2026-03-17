---
name: rebuild
description: Rebuild and restart the Docker Compose stack
disable-model-invocation: true
allowed-tools: Bash
argument-hint: "[service]"
---

Rebuild and restart the Docker Compose services.

If $ARGUMENTS is provided, rebuild only that service:
```bash
docker compose up --build --force-recreate -d $ARGUMENTS
```

Otherwise rebuild everything:
```bash
docker compose up --build --force-recreate -d
```

Then wait 15 seconds and check all services are healthy:
```bash
docker compose ps
```

If any service is unhealthy, show its logs with `docker compose logs <service> --tail 30`.