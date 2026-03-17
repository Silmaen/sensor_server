---
name: logs
description: Show recent logs from Docker services or the application log file
disable-model-invocation: true
allowed-tools: Bash, Read
argument-hint: "[service|app] [lines]"
---

Show recent logs.

- If $ARGUMENTS is "app" or empty: read the last N lines from `data/sensor_server/logs/sensor_server.log` (structured JSON).
- Otherwise treat $ARGUMENTS as a Docker service name and run: `docker compose logs $0 --tail ${1:-30}`
- Default number of lines: 30.

Parse JSON log entries for readability when showing the app log.