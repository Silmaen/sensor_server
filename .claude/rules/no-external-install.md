# No local tool installation required

- The user does NOT have project tools installed locally (mosquitto, postgres, etc.).
- All commands must run inside Docker containers via `docker compose exec`.
- Credentials must be read from `.env` before running any command — never assume defaults.
- Generated files (passwd, .mo, staticfiles) are created at container startup, not at build time.