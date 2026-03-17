---
name: manage
description: Run a Django management command inside the web container
disable-model-invocation: true
allowed-tools: Bash
argument-hint: "<command> [args...]"
---

Run a Django management command inside the web container:

```bash
docker compose exec web python manage.py $ARGUMENTS
```

Common commands:
- `shell` — interactive Python shell
- `migrate` — run database migrations
- `makemigrations` — create new migrations
- `makemessages -l fr` — extract translation strings
- `compilemessages` — compile .po to .mo
- `ensure_superuser` — create superuser from env vars